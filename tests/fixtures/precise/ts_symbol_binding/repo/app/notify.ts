import { send } from "@/lib/mail";

export function notify() {
  return send("ops@example.test", "done");
}
