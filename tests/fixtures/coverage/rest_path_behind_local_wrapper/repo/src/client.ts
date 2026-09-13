const API_VERSION = "v22";

type RequestOptions = {
  accessToken: string;
  path: string;
  method?: "GET" | "POST";
};

const callApi = async <T>({ path, method = "GET" }: RequestOptions): Promise<T> => {
  const response = await fetch(`https://googleads.googleapis.com/${API_VERSION}/${path}`, {
    method,
  });
  return (await response.json()) as T;
};

export class AccountsClient {
  constructor(private accessToken: string) {}

  async listAccessible() {
    return callApi<{ resourceNames?: string[] }>({
      accessToken: this.accessToken,
      path: "customers:listAccessibleCustomers",
    });
  }

  async searchStream(customerId: string) {
    return callApi<{ results?: unknown[] }>({
      accessToken: this.accessToken,
      path: `customers/${customerId}/googleAds:searchStream`,
      method: "POST",
    });
  }
}
