// Process-local receipts are issued only after the parser's complete decode.
// Persisted job fields cannot grant this shortcut after a restart. The import
// still verifies the copied bytes against this SHA-256 and size.
const verified=new Set();
const identity=o=>JSON.stringify([o.sha256,o.size,o.mimeType,o.kind,o.width,o.height,o.durationMs]);
export function rememberDecodedParserMedia(output){
 if(!/^[a-f0-9]{64}$/.test(output?.sha256)||!(output.size>0))throw Error('解析媒体校验记录无效');
 verified.add(identity(output));if(verified.size>128)verified.delete(verified.values().next().value);return output;
}
export function hasDecodedParserMedia(output){return !!output&&verified.has(identity(output));}
