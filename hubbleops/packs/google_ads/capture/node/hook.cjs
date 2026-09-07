const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");

const grpc = /^\/?google\.ads\.googleads\.(v[0-9]+)\.services\.([A-Za-z][A-Za-z0-9]*Service)\/([A-Za-z][A-Za-z0-9]*)$/;
const rest = /^\/?(v[0-9]+)\/customers\/[^/?]+\/googleAds:(searchStream|search|mutate)(?:\?.*)?$/;

function target(path) {
  let match = grpc.exec(path);
  if (match) return [match[1], match[2], match[3], "grpc"];
  match = rest.exec(path);
  if (!match) return null;
  const methods = {search: "Search", searchStream: "SearchStream", mutate: "Mutate"};
  return [match[1], "GoogleAdsService", methods[match[2]], "rest"];
}

function stack() {
  const priorLimit = Error.stackTraceLimit;
  Error.stackTraceLimit = 300;
  const lines = new Error().stack.split("\n").slice(3);
  Error.stackTraceLimit = priorLimit;
  const frames = lines.slice(0, 255).map((line) => {
    const normalized = line.replaceAll("\\", "/");
    const repository = normalized.includes("/workspace/");
    const path = repository ? normalized.split("/workspace/", 2)[1] : `<runtime>/${normalized.trim()}`;
    return {kind: repository ? "repository" : "runtime", path, line: null, function: null};
  });
  if (lines.length > 255) frames.push({kind: "truncation", path: "<runtime>/truncated", line: null, function: null, omitted: lines.length - 255});
  return frames;
}

function emit(path) {
  const matched = target(path);
  const destination = process.env.HUBBLEOPS_EVENT_PATH;
  if (!matched || !destination) return;
  const event = {version: matched[0], service: matched[1], method: matched[2], request_text: null, request_type: matched[3], stack: stack(), ts: new Date().toISOString(), mode: "hook"};
  fs.appendFileSync(destination, `${JSON.stringify(event)}\n`, {mode: 0o600});
}

function install(module) {
  const original = module.request;
  module.request = function wrapped(options, callback) {
    const path = typeof options === "string" ? new URL(options).pathname : options.path || options.pathname || "/";
    emit(path);
    return original.call(this, options, callback);
  };
}

function attest() {
  const destination = process.env.HUBBLEOPS_INSTALL_PATH;
  const nonce = process.env.HUBBLEOPS_INSTALL_NONCE;
  if (!destination || !nonce) return;
  fs.appendFileSync(destination, `${JSON.stringify({language: "node", nonce})}\n`, {mode: 0o600});
}

install(http);
install(https);
attest();
