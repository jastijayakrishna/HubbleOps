export const ENDPOINT = "https://googleads.googleapis.com/v22";

export function upload(path: string) {
  return fetch(`${ENDPOINT}/${path}`, { method: "POST" });
}
