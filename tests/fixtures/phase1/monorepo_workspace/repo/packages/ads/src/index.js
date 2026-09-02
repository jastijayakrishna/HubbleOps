const { GoogleAdsApi } = require('google-ads-api');

const client = new GoogleAdsApi({ apiVersion: 'v22' });

module.exports = { client };
