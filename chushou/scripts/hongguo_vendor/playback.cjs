// GPL-3.0-only. Retained local MP4 playback preparation from Fruitknife 2.0.0.
// Network retrieval now uses guoapp; this module has no network or account access.
const fs=require('node:fs'),crypto=require('node:crypto');
function aes128EcbEncrypt(key16, data) {
  const cipher = crypto.createCipheriv('aes-128-ecb', key16, null);
  cipher.setAutoPadding(false);
  return Buffer.concat([cipher.update(data), cipher.final()]);
}

/**
 * 生成 CENC-AES-CTR 的密钥流
 *   - 8 字节 IV：前 8 字节为 nonce，后 8 字节为大端计数器（从 0 开始）
 *   - 16 字节 IV：整个 16 字节作为一个 128 位大端计数器，逐块 +1
 */
function buildKeystream(key, iv, length) {
  if (length <= 0) return Buffer.alloc(0);
  const nblocks = Math.ceil(length / 16);
  const blocks = Buffer.alloc(nblocks * 16);
  if (iv.length >= 16) {
    const mask = (1n << 128n) - 1n;
    let base = 0n;
    for (let i = 0; i < 16; i++) base = (base << 8n) | BigInt(iv[i]);
    for (let k = 0; k < nblocks; k++) {
      const v = (base + BigInt(k)) & mask;
      blocks.writeBigUInt64BE(v >> 64n, k * 16);
      blocks.writeBigUInt64BE(v & 0xffffffffffffffffn, k * 16 + 8);
    }
  } else {
    for (let k = 0; k < nblocks; k++) {
      iv.copy(blocks, k * 16, 0, 8);
      blocks.writeBigUInt64BE(BigInt(k), k * 16 + 8);
    }
  }
  return aes128EcbEncrypt(key, blocks);
}

/**
 * CENC-AES-CTR 解密 sample
 * subs 为 senc 里的 subsample 列表 [[clearBytes, encBytes], ...]；
 * 没有 subsample 信息时按整段加密处理（兼容原有行为）。
 */
function decryptSample(key, nonce, cipher, subs) {
  if (!cipher || cipher.length === 0) return Buffer.alloc(0);

  if (!subs || subs.length === 0) {
    const ks = buildKeystream(key, nonce, cipher.length);
    const plain = Buffer.alloc(cipher.length);
    for (let i = 0; i < cipher.length; i++) {
      plain[i] = cipher[i] ^ ks[i];
    }
    return plain;
  }

  // subsample 模式：clear 段原样保留，只有 enc 段消耗密钥流（同一 sample 内连续）
  let encTotal = 0;
  for (const s of subs) encTotal += s[1] | 0;
  const ks = buildKeystream(key, nonce, encTotal);
  const plain = Buffer.alloc(cipher.length);
  let src = 0;
  let kpos = 0;
  for (const [clear, enc] of subs) {
    const cLen = Math.max(0, Math.min(clear | 0, cipher.length - src));
    if (cLen > 0) {
      cipher.copy(plain, src, src, src + cLen);
      src += cLen;
    }
    const eLen = Math.max(0, Math.min(enc | 0, cipher.length - src));
    for (let i = 0; i < eLen; i++) {
      plain[src + i] = cipher[src + i] ^ ks[kpos + i];
    }
    src += eLen;
    kpos += eLen;
  }
  if (src < cipher.length) cipher.copy(plain, src, src, cipher.length);
  return plain;
}

/**
 * 从 stbl -> stsd -> sample entry -> sinf/schi/tenc 读取加密参数
 * 返回 { ivSize, constantIv }，读不到时回退到 8 字节 per-sample IV
 */
function readTencInfo(data, stbl) {
  const info = { ivSize: 8, constantIv: null };
  const stsd = (stbl.children || []).find((b) => b.typ === 'stsd');
  if (!stsd) return info;

  const stsdEnd = stsd.off + stsd.size;
  const p = stsd.off + 16; // FullBox(4) + entry_count(4)
  if (p + 8 > stsdEnd) return info;
  const esize = data.readUInt32BE(p);
  if (esize < 8 || p + esize > stsdEnd) return info;

  const etyp = data.subarray(p + 4, p + 8).toString('latin1');
  let hdrSize = 78;
  if (etyp === 'enca') {
    hdrSize = 28;
    if (p + 18 <= p + esize) {
      const audioVer = data.readUInt16BE(p + 16);
      if (audioVer === 1) hdrSize = 44;
      else if (audioVer === 2) hdrSize = 64;
    }
  }

  let tencOff = -1;
  forEachChildBox(data, p + 8 + hdrSize, p + esize, (typ, off, size, hdr) => {
    if (typ !== 'sinf') return;
    forEachChildBox(data, off + hdr, off + size, (t2, o2, s2, h2) => {
      if (t2 !== 'schi') return;
      forEachChildBox(data, o2 + h2, o2 + s2, (t3, o3) => {
        if (t3 === 'tenc') tencOff = o3;
      });
    });
  });
  if (tencOff < 0) return info;

  const q = tencOff + 8; // 跳过 size + 'tenc'
  const isProtected = data[q + 6];
  const perSampleIvSize = data[q + 7];
  if (!isProtected) return info;
  if (perSampleIvSize > 0) {
    info.ivSize = perSampleIvSize;
  } else {
    const civSize = data[q + 24];
    info.constantIv = data.subarray(q + 25, q + 25 + civSize);
    info.ivSize = civSize;
  }
  return info;
}

/**
 * 解析 senc：每个 sample 的 IV + 可选的 subsample 表
 */
function parseSencSamples(data, senc, count, ivSize, constantIv) {
  const samples = [];
  const boxEnd = senc.off + senc.size;
  const flags = data.readUIntBE(senc.off + 9, 3);
  const hasSub = (flags & 0x02) !== 0;
  let p = senc.off + 16;

  for (let i = 0; i < count; i++) {
    if (!constantIv && p + ivSize > boxEnd) break;
    let iv;
    if (constantIv) {
      iv = constantIv;
    } else {
      iv = data.subarray(p, p + ivSize);
      p += ivSize;
    }
    let subs = null;
    if (hasSub) {
      if (p + 2 > boxEnd) break;
      const ns = data.readUInt16BE(p);
      p += 2;
      subs = [];
      for (let j = 0; j < ns; j++) {
        if (p + 6 > boxEnd) break;
        subs.push([data.readUInt16BE(p), data.readUInt32BE(p + 2)]);
        p += 6;
      }
    }
    samples.push({ iv, subs });
  }
  return samples;
}

/**
 * MP4 Box 结构解析
 */
function parseBoxes(data, start, end) {
  const boxes = [];
  let off = start;
  while (off + 8 <= end) {
    let size = data.readUInt32BE(off);
    const typ = data.subarray(off + 4, off + 8).toString('latin1');
    let hdr = 8;
    if (size === 1) {
      size = Number(data.readBigUInt64BE(off + 8));
      hdr = 16;
    } else if (size === 0) {
      size = end - off;
    }
    if (size < hdr || off + size > end) break;
    let children = null;
    if (['moov', 'trak', 'mdia', 'minf', 'stbl', 'edts', 'dinf', 'udta', 'meta'].includes(typ)) {
      children = parseBoxes(data, off + hdr, off + size);
    }
    boxes.push({ typ, off, size, hdr, children });
    off += size;
  }
  return boxes;
}

/**
 * 遍历一个 box 范围内的直接子 box
 * 回调参数：(typ, off, size, hdr)
 */
function forEachChildBox(data, start, end, cb) {
  let off = start;
  while (off + 8 <= end) {
    let size = data.readUInt32BE(off);
    const typ = data.subarray(off + 4, off + 8).toString('latin1');
    let hdr = 8;
    if (size === 1) {
      if (off + 16 > end) break;
      size = Number(data.readBigUInt64BE(off + 8));
      hdr = 16;
    } else if (size === 0) {
      size = end - off;
    }
    if (size < hdr || off + size > end) break;
    cb(typ, off, size, hdr);
    off += size;
  }
}

/**
 * 还原加密 sample entry（encv/enca）解密后应该使用的真实编码格式
 *
 * CENC 会把原始 sample entry 的类型藏进 sinf/frma。之前直接写死 hvc1/mp4a，
 * 会把 hev1（参数集在码流里）、avc1、ec-3 等一律标错，播放器按 hvc1 规则
 * 去 hvcC 找参数集找不到，就黑屏/“无法播放”。
 *
 * 优先取 frma 的原始 fourcc；frma 缺失时按 entry 内的配置盒推断；
 * 再不行才回退到 encv→hvc1 / enca→mp4a。
 */
function detectSampleEntryType(data, p, esize, hdrSize, encType) {
  let orig = '';
  let hasHvcC = false;
  let hasAvcC = false;
  let hasDac3 = false;
  let hasDec3 = false;
  let hasDops = false;

  forEachChildBox(data, p + 8 + hdrSize, p + esize, (typ, off, size, hdr) => {
    if (typ === 'hvcC') hasHvcC = true;
    else if (typ === 'avcC') hasAvcC = true;
    else if (typ === 'dac3') hasDac3 = true;
    else if (typ === 'dec3') hasDec3 = true;
    else if (typ === 'dOps') hasDops = true;
    else if (typ === 'sinf') {
      forEachChildBox(data, off + hdr, off + size, (t2, o2, s2, h2) => {
        if (t2 === 'frma' && !orig && h2 + 4 <= s2) {
          orig = data.subarray(o2 + h2, o2 + h2 + 4).toString('latin1');
        }
      });
    }
  });

  // frma 里的 fourcc 必须可打印，且不能是加密类型本身
  if (orig && /^[\x20-\x7e]{4}$/.test(orig) && orig !== 'encv' && orig !== 'enca') {
    return orig;
  }

  if (encType === 'enca') {
    if (hasDec3) return 'ec-3';
    if (hasDac3) return 'ac-3';
    if (hasDops) return 'opus';
    return 'mp4a';
  }
  if (hasHvcC) return 'hvc1';
  if (hasAvcC) return 'avc1';
  return encType === 'encv' ? 'hvc1' : 'mp4a';
}

/**
 * 重建 moov box
 */
function rebuildMoov(data, moovOff, moovSize, top) {
  const moovBox = top.find(b => b.typ === 'moov');
  if (!moovBox) throw new Error('no moov box');

  function clone(box) {
    return {
      typ: box.typ,
      off: box.off,
      size: box.size,
      hdr: box.hdr,
      children: box.children ? box.children.map(clone) : null
    };
  }

  function prune(box) {
    if (!box.children) return box;
    const keep = [];
    for (const c of box.children) {
      if (['senc', 'saio', 'saiz', 'sgpd', 'sbgp'].includes(c.typ)) continue;
      keep.push(prune(c));
    }
    box.children = keep;
    return box;
  }

  const tree = prune(clone(moovBox));

  function* walkChildren(box) {
    for (const c of (box.children || [])) {
      yield c;
      for (const x of walkChildren(c)) yield x;
    }
  }

  const patches = {};
  for (const b of walkChildren(tree)) {
    if (b.typ === 'stsd') {
      const content = b.off + 8;
      const count = data.readUInt32BE(content + 4);
      let p = content + 8;
      const entries = [];
      for (let i = 0; i < count; i++) {
        let esize = data.readUInt32BE(p);
        const etyp = data.subarray(p + 4, p + 8).toString('latin1');
        if (etyp === 'encv' || etyp === 'enca') {
          // 视觉 sample entry 固定头 78 字节；音频固定头 28 字节，
          // QuickTime v1/v2 会在固定头里再追加 16/36 字节
          let hdrSize = 78;
          if (etyp === 'enca') {
            hdrSize = 28;
            if (p + 18 <= p + esize) {
              const audioVer = data.readUInt16BE(p + 16);
              if (audioVer === 1) hdrSize = 44;
              else if (audioVer === 2) hdrSize = 64;
            }
          }
          // 关键：还原真实编码格式，而不是写死 hvc1/mp4a
          const newType = detectSampleEntryType(data, p, esize, hdrSize, etyp);
          const entry = Buffer.alloc(8 + hdrSize);
          entry.writeUInt32BE(0, 0);
          entry.write(newType, 4, 4, 'latin1');
          data.copy(entry, 8, p + 8, p + 8 + hdrSize);

          let entryExtra = Buffer.alloc(0);
          let q = p + 8 + hdrSize;
          while (q + 8 <= p + esize) {
            let s2 = data.readUInt32BE(q);
            const t2 = data.subarray(q + 4, q + 8).toString('latin1');
            if (s2 === 1) {
              s2 = Number(data.readBigUInt64BE(q + 8));
            } else if (s2 === 0) {
              s2 = p + esize - q;
            }
            if (t2 !== 'sinf') {
              entryExtra = Buffer.concat([entryExtra, data.subarray(q, q + s2)]);
            }
            q += s2;
          }
          const fullEntry = Buffer.concat([entry, entryExtra]);
          fullEntry.writeUInt32BE(fullEntry.length, 0);
          entries.push(fullEntry);
        } else {
          entries.push(data.subarray(p, p + esize));
        }
        p += esize;
      }
      const stsdHdr = Buffer.alloc(16);
      data.copy(stsdHdr, 0, b.off, content + 8);
      stsdHdr.writeUInt32BE(entries.length, 12);
      const fullStsd = Buffer.concat([stsdHdr, ...entries]);
      fullStsd.writeUInt32BE(fullStsd.length, 0);
      patches[b.off] = fullStsd;
    }
  }

  function serialize(box) {
    if (!box.children) {
      return patches[box.off] || data.subarray(box.off, box.off + box.size);
    }
    const body = Buffer.concat(box.children.map(serialize));
    const hdrb = Buffer.alloc(box.hdr);
    data.copy(hdrb, 0, box.off, box.off + box.hdr);
    if (box.hdr === 8) {
      hdrb.writeUInt32BE(8 + body.length, 0);
    } else {
      hdrb.writeUInt32BE(1, 0);
      hdrb.writeBigUInt64BE(BigInt(16 + body.length), 8);
    }
    return Buffer.concat([hdrb, body]);
  }

  const first = serialize(tree);
  const delta = moovSize - first.length;

  // stco/co64 是文件绝对偏移：
  //   - moov 在 mdat 之前：moov 变小后，后面所有数据整体前移，偏移要减去 delta
  //   - moov 在 mdat 之后（红果/ffmpeg 默认布局）：mdat 在前面，偏移不能动
  // 以前无条件按「v >= delta」减，多轨文件会整片错位导致花屏。
  if (delta !== 0) {
    const moovEnd = moovOff + moovSize;
    for (const b of walkChildren(tree)) {
      if (b.typ === 'stco') {
        const nc = data.readUInt32BE(b.off + 12);
        for (let i = 0; i < nc; i++) {
          const pos = b.off + 16 + i * 4;
          const v = data.readUInt32BE(pos);
          if (v >= moovEnd) data.writeUInt32BE(v - delta, pos);
        }
      } else if (b.typ === 'co64') {
        const nc = data.readUInt32BE(b.off + 12);
        for (let i = 0; i < nc; i++) {
          const pos = b.off + 16 + i * 8;
          const v = Number(data.readBigUInt64BE(pos));
          if (v >= moovEnd) data.writeBigUInt64BE(BigInt(v - delta), pos);
        }
      }
    }
  }

  return serialize(tree);
}

/**
 * 解密 MP4 文件并保存
 */
function decryptMp4File(srcPath, dstPath, key) {
  const data = fs.readFileSync(srcPath);
  const total = data.length;
  const top = parseBoxes(data, 0, total);
  const moov = top.find(b => b.typ === 'moov');
  if (!moov) throw new Error('no moov box found in MP4');

  function getBox(boxes, typ) {
    return (boxes || []).find(b => b.typ === typ);
  }

  function* walkBoxes(boxes) {
    for (const b of boxes) {
      if (b.children) {
        for (const x of walkBoxes(b.children)) yield x;
      }
      yield b;
    }
  }

  const stbls = Array.from(walkBoxes(top)).filter(b => b.typ === 'stbl');
  const trackInfo = [];
  for (const stbl of stbls) {
    const stsz = getBox(stbl.children, 'stsz');
    const stco = getBox(stbl.children, 'stco');
    const stsc = getBox(stbl.children, 'stsc');
    const senc = getBox(stbl.children, 'senc');
    if (!stsz || !stco || !stsc) continue;
    const ss = data.readUInt32BE(stsz.off + 12);
    const n = data.readUInt32BE(stsz.off + 16);
    let sizes = [];
    if (ss === 0) {
      for (let i = 0; i < n; i++) {
        sizes.push(data.readUInt32BE(stsz.off + 20 + i * 4));
      }
    } else {
      sizes = new Array(n).fill(ss);
    }

    const nc = data.readUInt32BE(stco.off + 12);
    const chunkOffs = [];
    for (let i = 0; i < nc; i++) {
      chunkOffs.push(data.readUInt32BE(stco.off + 16 + i * 4));
    }

    const ns = data.readUInt32BE(stsc.off + 12);
    const stscTab = [];
    for (let i = 0; i < ns; i++) {
      stscTab.push([
        data.readUInt32BE(stsc.off + 16 + i * 12),
        data.readUInt32BE(stsc.off + 20 + i * 12),
        data.readUInt32BE(stsc.off + 24 + i * 12),
      ]);
    }

    const tenc = readTencInfo(data, stbl);
    const samples = senc
      ? parseSencSamples(data, senc, sizes.length, tenc.ivSize, tenc.constantIv)
      : [];

    const chunkSpc = {};
    for (let i = 0; i < stscTab.length; i++) {
      const fc = stscTab[i][0];
      const spc = stscTab[i][1];
      const nxt = (i + 1 < stscTab.length) ? stscTab[i + 1][0] : (nc + 1);
      for (let ci = fc - 1; ci < nxt - 1; ci++) {
        chunkSpc[ci] = spc;
      }
    }

    const sampleOffs = [];
    let si = 0;
    for (let ci = 0; ci < nc; ci++) {
      let off = chunkOffs[ci];
      const spc = chunkSpc[ci] || 1;
      for (let s = 0; s < spc; s++) {
        if (si >= n) break;
        sampleOffs.push(off);
        off += sizes[si];
        si++;
      }
    }

    trackInfo.push({ sizes, offs: sampleOffs, samples });
  }

  for (const ti of trackInfo) {
    for (let i = 0; i < ti.offs.length; i++) {
      const sample = ti.samples[i];
      if (!sample || !sample.iv || sample.iv.length === 0) continue;
      const off = ti.offs[i];
      const sz = ti.sizes[i];
      if (off + sz > total) continue;
      const cipher = data.subarray(off, off + sz);
      const plain = decryptSample(key, sample.iv, cipher, sample.subs);
      plain.copy(data, off);
    }
  }

  const newMoov = rebuildMoov(data, moov.off, moov.size, top);
  const out = [];
  let off = 0;
  while (off < total) {
    let size = data.readUInt32BE(off);
    const typ = data.subarray(off + 4, off + 8).toString('latin1');
    let hdr = 8;
    if (size === 1) {
      size = Number(data.readBigUInt64BE(off + 8));
      hdr = 16;
    } else if (size === 0) {
      size = total - off;
    }
    if (typ === 'moov') {
      out.push(newMoov);
    } else {
      out.push(data.subarray(off, off + size));
    }
    off += size;
  }

  const finalBuf = Buffer.concat(out);
  fs.writeFileSync(dstPath, finalBuf);
}

module.exports={decryptMp4File};

