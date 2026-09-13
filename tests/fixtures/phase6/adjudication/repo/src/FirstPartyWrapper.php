<?php
namespace Shop\Ads;

use Shop\Ads\Local\GoogleAdsClient;

class FirstPartyWrapper {
	private $client;

	public function __construct( GoogleAdsClient $client ) {
		$this->client = $client;
	}
}
