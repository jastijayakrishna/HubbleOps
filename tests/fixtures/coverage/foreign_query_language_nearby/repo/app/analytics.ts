import { upload } from "../lib/ads/client";

const CLICKS = "SELECT click_event.id FROM click_event WHERE click_event.status = 1";
const ORDERS = "SELECT warehouse_order.id FROM warehouse_order LIMIT 10";

export async function report(rows: string[]) {
  await upload("customers/1/conversionUploads:ingest");
  return [CLICKS, ORDERS, rows.length];
}
