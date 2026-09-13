export interface Transport {
  send(path: string, version: string): Promise<Response>;
}
