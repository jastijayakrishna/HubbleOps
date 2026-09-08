const http = require("node:http");

function reportingGatewaySearch(query) {
  const request = http.request(
    {
      host: "127.0.0.1",
      port: 9,
      path: "/google.ads.googleads.v23.services.GoogleAdsService/Search",
      method: "POST",
    },
    () => {},
  );
  request.on("error", () => {});
  request.end(query);
}

function dailyCampaignReport() {
  reportingGatewaySearch("SELECT campaign.id FROM campaign");
}

dailyCampaignReport();
