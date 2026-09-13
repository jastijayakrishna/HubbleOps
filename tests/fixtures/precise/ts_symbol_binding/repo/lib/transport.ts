export function send(path: string, version: string) {
  return fetch(`https://googleads.googleapis.com/${version}/${path}`);
}
