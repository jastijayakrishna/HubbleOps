export const SCOPES = [
  "https://www.googleapis.com/auth/adwords",
  "https://www.googleapis.com/auth/datamanager",
].join(" ");

export function headers(token: string, loginCustomerId?: string) {
  const base: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "developer-token": process.env.GOOGLE_ADS_DEVELOPER_TOKEN!,
  };
  if (loginCustomerId) {
    base["login-customer-id"] = loginCustomerId;
  }
  return base;
}

export function conversionActionPrefix(customerId: string) {
  return `customers/${customerId}/conversionActions/`;
}

export function readFailure(payload: any) {
  return payload?.error?.details?.[0]?.errors?.[0]?.errorCode?.authorizationError;
}
