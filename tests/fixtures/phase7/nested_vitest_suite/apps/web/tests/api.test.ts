import { describe, expect, it } from "vitest";

import { apiVersion, searchUrl } from "../src/api";

describe("the Google Ads search url", () => {
  it("addresses the customer's search endpoint", () => {
    expect(searchUrl("123")).toContain("/customers/123/googleAds:search");
  });

  it("carries whatever version the constant names", () => {
    expect(searchUrl("123")).toContain(apiVersion());
  });
});
