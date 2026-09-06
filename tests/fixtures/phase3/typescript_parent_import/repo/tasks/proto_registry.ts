import { protos } from "google-ads-node";

import { VERSION_TOKEN } from "../lib/version_token";

const ACTIVE_TOKEN = VERSION_TOKEN;

export const SERVICE_MAP = protos.google.ads.googleads[ACTIVE_TOKEN].services;
