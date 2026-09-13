import { API_RELEASE, send } from "@/lib/index";

export function report(id: string) {
  return send(`customers/${id}/googleAds:search`, API_RELEASE);
}
