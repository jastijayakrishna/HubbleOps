export function deliver(to: string, body: string) {
  return `${to}:${body}`;
}

export function notify() {
  return deliver("ops@example.test", "done");
}
