const { GoogleAdsApi } = require('google-ads-api');

function buildClient() {
  return new GoogleAdsApi({
    apiVersion: process.env.ADS_API_VERSION,
    developer_token: process.env.GOOGLE_ADS_DEVELOPER_TOKEN,
  });
}

module.exports = { buildClient };
