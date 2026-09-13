export const ADS_VERSION = "v22";

export function endpoint(path: string) {
  return `https://googleads.googleapis.com/${ADS_VERSION}/${path}`;
}
