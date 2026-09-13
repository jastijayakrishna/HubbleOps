export const ADS_ENDPOINT = "https://googleads.googleapis.com/v22/customers";

export function endpoint(path: string) {
  return `${ADS_ENDPOINT}/${path}`;
}
