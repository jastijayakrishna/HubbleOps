export const FAILURE_TYPE =
  "type.googleapis.com/google.ads.googleads.v22.errors.GoogleAdsFailure";

export function isFailure(detail: { "@type"?: string }) {
  return detail["@type"] === FAILURE_TYPE;
}
