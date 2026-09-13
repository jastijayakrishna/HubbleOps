<?php
namespace Shop\Ads;

use Google\Ads\GoogleAds\V22\Services\SearchGoogleAdsRequest;

class BoundImport {
	public function build() {
		return new SearchGoogleAdsRequest();
	}
}
