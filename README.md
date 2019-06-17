# lambda-dns-axfr: Lambda function to perform DNS zone transfer to Route 53 Hosted Zone

Fork of https://github.com/awslabs/aws-lambda-mirror-dns-function with the following changes:

- Python 3.7 support
- DNS server failover
- Multiple DNS zones into single Route 53 hosted zone
- Match DNS zone origin to Route 53 hosted zone (on-premise zone `corp.contoso.com` => Route 53 hosted zone `contoso.com`)

## SAM Template

There is an example SAM template available in jsonnet.

## License

Apache 2.0

- Copyright 2016 Amazon.com, Inc. or its affiliates. All Rights Reserved.
- Copyright 2019 Sorah Fukumori.
