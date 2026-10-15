import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";

const PINNED_DISPATCHER = Symbol("shensi.pinned-public-dispatcher");
const normalizedHostname = (value = "") => String(value).trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");

const normalizedAddresses = (addresses = []) => [...new Map((Array.isArray(addresses) ? addresses : [])
  .map((record) => ({
    address: String(record?.address || "").trim(),
    family: Number(record?.family) === 6 ? 6 : 4,
  }))
  .filter((record) => record.address)
  .map((record) => [`${record.family}:${record.address}`, record])).values()];

const createPinnedLookup = ({ hostname = "", addresses = [] } = {}) => {
  const expectedHostname = normalizedHostname(hostname);
  const resolvedAddresses = normalizedAddresses(addresses);
  if (!expectedHostname || !resolvedAddresses.length) throw new Error("公共网络请求缺少已经验证的 DNS 地址");
  let cursor = 0;
  return (requestedHostname, options = {}, callback) => {
    const actualHostname = normalizedHostname(requestedHostname);
    if (actualHostname !== expectedHostname) {
      const error = new Error("公共网络请求尝试连接未经验证的主机");
      error.code = "SHENSI_UNPINNED_HOST";
      callback(error);
      return;
    }
    const family = Number(options?.family) || 0;
    const eligible = family === 4 || family === 6
      ? resolvedAddresses.filter((record) => record.family === family)
      : resolvedAddresses;
    if (!eligible.length) {
      const error = new Error("公共网络请求没有匹配的已验证地址族");
      error.code = "SHENSI_PINNED_ADDRESS_FAMILY_MISSING";
      callback(error);
      return;
    }
    if (options?.all === true) {
      callback(null, eligible.map((record) => ({ ...record })));
      return;
    }
    const selected = eligible[cursor % eligible.length];
    cursor += 1;
    callback(null, selected.address, selected.family);
  };
};

export const createPinnedPublicDispatcher = ({ hostname = "", addresses = [] } = {}) => {
  const expectedHostname = normalizedHostname(hostname);
  const resolvedAddresses = normalizedAddresses(addresses);
  const lookup = createPinnedLookup({ hostname: expectedHostname, addresses: resolvedAddresses });
  return {
    [PINNED_DISPATCHER]: true,
    hostname: expectedHostname,
    addresses: resolvedAddresses,
    lookup,
    close: async () => {},
  };
};

const requestHeaders = (value = {}) => {
  if (typeof Headers !== "undefined") return Object.fromEntries(new Headers(value).entries());
  return value;
};

export const fetchPinnedPublicUrl = async (input, {
  dispatcher,
  headers = {},
  method = "GET",
  signal,
  maxResponseBytes = 4 * 1024 * 1024,
} = {}) => {
  const url = input instanceof URL ? input : new URL(String(input));
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("固定地址传输只支持 HTTP 或 HTTPS");
  if (!dispatcher?.[PINNED_DISPATCHER] || normalizedHostname(url.hostname) !== dispatcher.hostname) {
    const error = new Error("公共网络请求缺少与目标主机匹配的固定地址传输器");
    error.code = "SHENSI_UNPINNED_HOST";
    throw error;
  }
  const byteLimit = Math.max(1, Number(maxResponseBytes) || 4 * 1024 * 1024);
  const requestImpl = url.protocol === "https:" ? httpsRequest : httpRequest;
  return new Promise((resolve, reject) => {
    let settled = false;
    const finishReject = (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    };
    const request = requestImpl(url, {
      method: String(method || "GET").toUpperCase(),
      headers: requestHeaders(headers),
      lookup: dispatcher.lookup,
      signal,
    }, (response) => {
      const chunks = [];
      let total = 0;
      response.on("data", (chunk) => {
        if (settled) return;
        total += chunk.length;
        if (total > byteLimit) {
          const error = new Error("公共网络响应超过读取上限");
          error.code = "SHENSI_PUBLIC_RESPONSE_TOO_LARGE";
          response.destroy(error);
          finishReject(error);
          return;
        }
        chunks.push(chunk);
      });
      response.once("error", finishReject);
      response.once("end", () => {
        if (settled) return;
        settled = true;
        const status = Number(response.statusCode) || 500;
        const body = [101, 204, 205, 304].includes(status) ? null : Buffer.concat(chunks);
        resolve(new Response(body, {
          status,
          statusText: response.statusMessage || "",
          headers: response.headers,
        }));
      });
    });
    request.once("error", finishReject);
    request.end();
  });
};

export const closePinnedPublicDispatcher = async (dispatcher) => {
  if (!dispatcher || typeof dispatcher.close !== "function") return;
  try { await dispatcher.close(); } catch {}
};
