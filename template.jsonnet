{
  AWSTemplateFormatVersion: '2010-09-09',
  Transform: 'AWS::Serverless-2016-10-31',
  Description: 'DNS AXFR => Route 53 Hosted Zone',
  Resources: {
    DnsAxfrFunction: {
      Type: 'AWS::Serverless::Function',  // https://github.com/awslabs/serverless-application-model/blob/master/versions/2016-10-31.md#awsserverlessfunction
      Properties: {
        CodeUri: 'src/',
        Handler: 'lambda_function.lambda_handler',
        Role: 'arn:aws:iam::789035092620:role/LambdaDnsAxfr',
        Runtime: 'python3.7',
        Timeout: 20,
        Environment: {
          Variables: {
            DNSAXFR_DOMAIN: 'corp.contoso.com.,_msdcs.corp.contoso.com.',
            DNSAXFR_MASTER_DNS: '10.0.0.53,SRV _ldap._tcp.Default-First-Site-Name._sites.dc._msdcs.corp.contoso.com.',
            DNSAXFR_HOSTED_ZONE_ID: '',
            DNSAXFR_HOSTED_ZONE_NAME: 'contoso.com.',
            DNSAXFR_SERIAL_RECORD_NAME: '_dns-serial',
          },
        },
        VpcConfig: {
          SecurityGroupIds: [],
          SubnetIds: [],
        },
        Events: {
          DnsAxfrCron: {
            Type: 'Schedule',
            Properties: {
              Schedule: 'rate(12 minutes)',
            },
          },
        },
      },
    },
  },
  Outputs: {
    DnsAxfrFunction: {
      Description: 'Function ARN',
      Value: 'DnsAxfrFunction.Arn',
    },
  },
}
