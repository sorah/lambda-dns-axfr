# -*- coding: utf-8 -*-

# AWS Lambda function to mirror an on-premises DNS to Route 53 private hosted zone
# with allowing to mirror into a hosted zone with different origin
# (e.g. mirror 'activedirectory.example.org' on-premises zone to 'example.org' Route 53 zone)

# This script is a fork of https://github.com/awslabs/aws-lambda-mirror-dns-function
# with a modification for supporting multiple on-premise zones and single Route 53 hosted zone
# and Python 3 support.
#
#  Copyright 2016 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License"). You may not
#  use this file except in compliance with the License. A copy of the License is
#  located at
#      http://aws.amazon.com/apache2.0/
#
#  or in the "license" file accompanying this file. This file is distributed on
#  an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
#  express or implied. See the License for the specific language governing
#  permissions and limitations under the License.


import dns.exception
import dns.message
import dns.name
import dns.query
import dns.resolver
import dns.zone
import lookup_rdtype
from dns.rdataclass import *
from dns.rdatatype import *

# libraries that are available on Lambda
import re
import os
import sys
import boto3

# If you need to use a proxy server to access the Internet then hard code it 
# the details below, otherwise comment out or remove.
# os.environ["http_proxy"] = "10.10.10.10:3128"  # My on-premises proxy server
# os.environ["https_proxy"] = "10.10.10.10:3128"
# os.environ["no_proxy"] = "169.254.169.254"  # Don't proxy for meta-data service as Lambda  needs to get IAM credentials

# setup the boto3 client to talk to AWS APIs
route53 = boto3.client('route53')


# Function to create, update, delete records in Route 53
def update_resource_record(host_name, domain, rectype, changerec, ttl, action):
    if domain[-1] != '.':
        domain = domain + '.'

    if (rectype == 'NS' and host_name == '@'):
        return None


    if host_name == '@':
        host_name = ''
    elif host_name[-1] != '.':
        host_name = host_name + '.'
    fqdn = host_name + domain

    change = {
                'Action': action,
                'ResourceRecordSet': {
                    'Name': fqdn,
                    'Type': rectype,
                    'ResourceRecords': [],
                    'TTL': ttl
                }
            }

    for value in changerec:  # Build the recordset
        change['ResourceRecordSet']['ResourceRecords'].append({'Value': str(value)})
        print('%s: %s %s => %s (ttl %d)' % (action, rectype, fqdn, str(value), ttl))

    return change

def adjust_node_name(origin1, origin2, name):
    if name == '@':
        fqdn = str(origin1)
    elif name.endswith('.'):
        fqdn = name
    else:
        fqdn = name + '.' + str(origin1)
    fqdn = fqdn.rstrip('.')
    name2 = re.sub('\.?' + re.escape(str(origin2).rstrip('.')) + '$', '', fqdn)
    if name2.endswith(str(origin1).rstrip('.')):
        raise InvalidNodeComparison('%s (%s) => (%s)' % (fqdn, origin1, origin2))
    if name2 == '':
        return '@'
    else:
        return name2.lower()

def check_record_target(target, name, origin):
    if name == '@':
        fqdn = '.' + str(origin)
    else:
        fqdn = name + '.' + str(origin)
    fqdn = fqdn.rstrip('.')
    target = target.rstrip('.')
    return fqdn.endswith('.' + target) or fqdn == target

def convert_zone(domain, zone):
    new_zone = dns.zone.Zone(origin=(domain.rstrip('.') + '.'))
    for name in zone:
        new_name = adjust_node_name(zone.origin, domain, str(name))
        node = zone.get_node(name)
        for rdataset in node.rdatasets:
            new_rdataset = new_zone.find_rdataset(new_name, rdtype=rdataset.rdtype, create=True)
            for rdata in rdataset:
                new_rdata = dns.rdata.from_text(1, rdata.rdtype, rdata.to_text())

                if rdataset.rdtype == dns.rdatatype.CNAME:
                    if not new_rdata.target.is_absolute():
                        new_rdata = new_rdata.replace(target=dns.name.from_text(adjust_node_name(zone.origin, domain, str(new_rdata.target.derelativize(zone.origin))), origin=new_zone.origin))
                elif rdataset.rdtype == dns.rdatatype.MX:
                    if not new_rdata.exchange.is_absolute():
                        new_rdata = new_rdata.replace(exchange=dns.name.from_text(adjust_node_name(zone.origin, domain, str(new_rdata.exchange.derelativize(zone.origin))), origin=new_zone.origin))
                elif rdataset.rdtype == dns.rdatatype.NS:
                    if not new_rdata.target.is_absolute():
                        new_rdata = new_rdata.replace(target=dns.name.from_text(adjust_node_name(zone.origin, domain, str(new_rdata.target.derelativize(zone.origin))), origin=new_zone.origin))
                elif rdataset.rdtype == dns.rdatatype.SRV:
                    if not new_rdata.target.is_absolute():
                        new_rdata = new_rdata.replace(target=dns.name.from_text(adjust_node_name(zone.origin, domain, str(new_rdata.target.derelativize(zone.origin))), origin=new_zone.origin))

                new_rdataset.add(new_rdata, ttl=rdataset.ttl)
    return new_zone

# Perform a diff against the two zones and return difference set
def diff_zones(domain, zone1, zone2, ignore_ttl, domain_names_to_ignore):
    differences = []
    for node in zone1: # Process existing
        if not check_record_target(domain, str(node), zone1.origin):
            continue

        ignore = False
        for ignore_domain in domain_names_to_ignore:
            if check_record_target(ignore_domain, str(node), zone1.origin):
                ignore = True
                break
        if ignore:
            continue

        node1 = zone1.get_node(node)
        node2 = zone2.get_node(node)
        if not node2:
            for record1 in node1:
                changerec = []
                for value1 in record1:
                    changerec.append(value1)
                change = (str(node), record1.rdtype, changerec, record1.ttl, 'DELETE')
                if change not in differences:
                    differences.append(change)
        else:
            for record1 in node1:
                record2 = node2.get_rdataset(record1.rdclass, record1.rdtype)
                if record1 != record2:  # update record to new zone
                    changerec = []
                    if record2:
                        action = 'UPSERT'
                        for value2 in record2:
                            changerec.append(value2)
                    else:
                        action = 'DELETE'
                        for value1 in record1:
                            changerec.append(value1)
                    change = (str(node), record1.rdtype, changerec, record1.ttl, action)
                    if change and change not in differences:
                        differences.append(change)

    for node in zone2:
        if not check_record_target(domain, str(node), zone2.origin):
            continue

        ignore = False
        for ignore_domain in domain_names_to_ignore:
            if check_record_target(ignore_domain, str(node), zone1.origin):
                ignore = True
                break
        if ignore:
            continue

        node1 = zone1.get_node(node)
        node2 = zone2.get_node(node)
        if node1:
            for record2 in node2:
                record1 = node1.get_rdataset(record2.rdclass, record2.rdtype)
                if record2.rdtype == dns.rdatatype.SOA:
                    continue
                if record2.rdtype == dns.rdatatype.NS and str(node) == '@':
                    continue

                if not record1:  # Create new record
                    changerec = []
                    for value2 in record2:
                        changerec.append(value2)
                        change = (str(node), record2.rdtype, changerec, record2.ttl, 'UPSERT')
                        if change and change not in differences:
                            differences.append(change)
                elif record1 != record2:  # update record to new zone
                    changerec = []
                    for value2 in record2:
                        changerec.append(value2)

                    change = (str(node), record2.rdtype, changerec, record2.ttl, 'UPSERT')
                    if change and change not in differences:
                        differences.append(change)

                if record2.rdtype == dns.rdatatype.SOA or not record1:
                    continue
                elif not ignore_ttl and record2.ttl != record1.ttl:  # Check if the TTL has been updated
                    changerec = []
                    for value2 in record2:
                        changerec.append(value2)
                    change = (str(node), record2.rdtype, changerec, record2.ttl, 'UPSERT')
                    if change and change not in differences:
                        differences.append(change)
                elif record2.ttl != record1.ttl:
                    print('Ignoring TTL update for %s' % node)
        else:
            for record2 in node2:
                changerec = []
                for value2 in record2:
                    changerec.append(value2)
                    change = (str(node), record2.rdtype, changerec, record2.ttl, 'CREATE')
                    if change and change not in differences:
                        differences.append(change)

    return differences


def fetch_master_dns_server(server_names, domain_name):
    for rawname in server_names:
        names = [rawname]
        if rawname.startswith('SRV '):
            record_name = rawname.split(' ')[1]
            print('Resolving SRV %s' % (record_name))
            try:
                names = [str(rdata.target) for rdata in sorted(dns.resolver.query(record_name, 'SRV'), key=lambda x:x.priority)]
            except dns.exception.DNSException as err:
                print('! %s' % (err))
                continue
        for name in names:
            print('Testing %s' % name)
            try:
                address = dns.resolver.query(name)[0].address
                res = dns.query.udp(dns.message.make_query(domain_name, 'NS'), address, timeout=3)
                if res.rcode() == 0:
                    print('%s (%s) OK: %s' % (name, address, res))
                    return address
                else:
                    print('%s (%s) NG: rcode %d' % (name, address, res.rcode()))
            except dns.exception.DNSException as err:
                print('! %s' % (err))
    return None

# Main Handler for lambda function
def lambda_handler(event, context):
    # Setup configuration based on JSON formatted event data
    domain_names_str = event.get('Domain', os.environ.get('DNSAXFR_DOMAIN', None))
    master_dns_str= event.get('MasterDns', os.environ.get('DNSAXFR_MASTER_DNS', None))
    route53_zone_id = event.get('ZoneId', os.environ.get('DNSAXFR_HOSTED_ZONE_ID', None))
    route53_zone_name = event.get('ZoneName', os.environ.get('DNSAXFR_HOSTED_ZONE_NAME', None))
    serial_record_name = event.get('SerialRecordName', os.environ.get('DNSAXFR_SERIAL_RECORD_NAME', None))
    dry_run = event.get('DryRun', os.environ.get('DRY_RUN', 'False')) == 'True'
    ignore_ttl = event.get('IgnoreTTL', os.environ.get('DNSAXFR_IGNORE_TTL', 'False')) == 'True'
    if domain_names_str == None or master_dns_str == None or route53_zone_id == None or route53_zone_name == None or serial_record_name == None:
        print('configuration missing')
        os.exit(1)

    domain_names = domain_names_str.split(',')
    master_ip = fetch_master_dns_server(master_dns_str.split(','), domain_names[0])

    # Transfer the master zone file from the DNS server via AXFR
    master_zones = {}
    for domain_name in domain_names:
        print('Transferring zone %s from server %s ' % (domain_name, master_ip))
        master_zones[domain_name] = dns.zone.from_xfr(dns.query.xfr(master_ip, domain_name))

    # Read the zone from Route 53 via API and populate into zone object
    print('Getting records from Route 53')
    vpc_zone = dns.zone.Zone(origin=route53_zone_name)
    # vpc_recordset = route53.list_resource_record_sets(HostedZoneId=route53_zone_id)['ResourceRecordSets']
    paginator = route53.get_paginator('list_resource_record_sets').paginate(HostedZoneId=route53_zone_id)
    for page in paginator:
        vpc_recordset = page['ResourceRecordSets']
        for record in vpc_recordset:
            # Change the record name so that it doesn't have the domain name appended
            recordname = record['Name'].replace(route53_zone_name.rstrip('.') + '.', '')
            if recordname == '':
                recordname = "@"
            else:
                recordname = recordname.rstrip('.')
            rdataset = vpc_zone.find_rdataset(recordname, rdtype=str(record['Type']), create=True)
            for value in record['ResourceRecords']:
                rdata = dns.rdata.from_text(1, rdataset.rdtype, value['Value'])
                rdataset.add(rdata, ttl=int(record['TTL']))

    for domain_name, master_zone in master_zones.items():
        perform_mirror(domain_names, domain_name, master_zone, route53_zone_id, route53_zone_name, vpc_zone, serial_record_name, dry_run, ignore_ttl)

def perform_mirror(domain_names, domain_name, master_zone, route53_zone_id, route53_zone_name, vpc_zone, serial_record_name, dry_run, ignore_ttl):
    print('Mirroring Zone  %s to Route53 Hosted Zone %s' % (domain_name, route53_zone_name))
    soa = master_zone.get_rdataset('@', 'SOA')
    serial = soa[0].serial  # What's the current zone version on-prem

    serial_record_fqdn = '%s.%s' % (serial_record_name, domain_name)
    serial_record = adjust_node_name(master_zone.origin, route53_zone_name, serial_record_fqdn)
    vpc_serial_txt = vpc_zone.get_rdataset(serial_record, 'TXT')
    if vpc_serial_txt:
        vpc_serial = int(vpc_serial_txt[0].to_text().lstrip('"').rstrip('"'))
    else:
        vpc_serial = None

    domain_names_to_ignore = [n for n in domain_names if n != domain_name]

    if vpc_serial and (vpc_serial > serial):
        print('ERROR: Route 53 VPC serial %s for domain %s is greater than existing serial %s' % (str(vpc_serial), domain_name, str(serial)))
        sys.exit(1)
    else:
        print('Comparing SOA serial (R53=%s, XFR=%s)' % (vpc_serial, serial))
        master_zone_converted = convert_zone(route53_zone_name, master_zone)
        differences = diff_zones(domain_name, vpc_zone, master_zone_converted, ignore_ttl, domain_names_to_ignore)

        dns_changes = [update_resource_record(serial_record, route53_zone_name, 'TXT', ['"%d"' % serial], 5, 'UPSERT')]
        for host, rdtype, record, ttl, action in differences:
            if host == serial_record:
                continue
            if rdtype == dns.rdatatype.SOA:
                continue
            change = update_resource_record(host, route53_zone_name, lookup_rdtype.recmap(rdtype), record, ttl, action)
            if change != None:
                dns_changes.append(change)

        if len(dns_changes) == 1 and vpc_serial == serial:
            print('no change')
            return

        if not dry_run:
            change_batch = {
                'Comment': 'dns-axfr',
                'Changes': dns_changes,
            }
            print(route53.change_resource_record_sets(HostedZoneId=route53_zone_id, ChangeBatch=change_batch))
        else:
            print('dry-run')

    return 'SUCCESS: %s mirrored to Route 53 VPC serial %s' % (domain_name, str(serial))

if __name__ == '__main__':
    lambda_handler({}, None)
