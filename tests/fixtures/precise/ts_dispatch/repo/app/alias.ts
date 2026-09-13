import { deliver as post } from "@/lib/transport";

export function relay(id: string) {
  return post(`customers/${id}/googleAds:search`, "v24");
}
