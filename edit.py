# -*- coding: utf-8 -*-
import argparse
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

os.environ['PYWIKIBOT_DIR'] = os.path.dirname(os.path.realpath(__file__))
import pywikibot
from pywikibot.exceptions import EditConflictError

from config import config_page_name  # pylint: disable=E0611,W0614

parser = argparse.ArgumentParser()
parser.add_argument('--debug', action='store_true', dest='debug')
parser.add_argument('--dry-run', action='store_true', dest='dry_run', help='show diff without saving')
parser.set_defaults(debug=False, dry_run=False)
args = parser.parse_args()

FAIL_RETRY = 5
SECTION_COUNT = 4
REQUEST_REGEX = r'^(===[^=].*[^=]===[ \t]*)$'
TIME_REGEX =r'(\d{4})年(\d{1,2})月(\d{1,2})日 \(.\) (\d{2}):(\d{2}) \(UTC\)'


def converttime(m):
    return datetime(int(m[0]), int(m[1]), int(m[2]), int(m[3]), int(m[4]), tzinfo=timezone.utc).timestamp()


def fmt_time(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def split_sections(text):
    marker = uuid.uuid4().hex
    for i in range(1, SECTION_COUNT + 1):
        text = re.sub(
            r'^( *== *{} *== *)$'.format(re.escape(cfg['text{}'.format(i)])),
            marker + r'\1',
            text,
            flags=re.M,
        )
    text = text.split(marker)
    if len(text) != SECTION_COUNT + 1:
        print('split error')
        exit()
    return text


def save_page(page, text, summary):
    if args.dry_run:
        pywikibot.showDiff(page.text, text)
        print('dry run, not saved')
        return
    if args.debug:
        pywikibot.showDiff(page.text, text)
        print(summary)
        save = input('Save? ').lower()
    else:
        save = 'yes'
    if save in ['yes', 'y', '']:
        page.text = text
        page.save(summary=summary, minor=True)


print('The time now is {} (UTC)'.format(fmt_time(time.time())))

site = pywikibot.Site()
site.login()

config_page = pywikibot.Page(site, config_page_name)
cfg = config_page.text
cfg = json.loads(cfg)
if args.debug:
    print(json.dumps(cfg, indent=4, ensure_ascii=False))

if not cfg['enable']:
    print('disabled')
    exit()

retention_time = {}
for i in range(1, SECTION_COUNT + 1):
    retention_time[i] = cfg['retention_time_{}'.format(i)]
    print('{}. archive before {} ago ({})'.format(i, retention_time[i], fmt_time(time.time() - retention_time[i])))

ignore_before = time.time() - cfg['ignore_timestamp_older_than']
print('ignore time before {}'.format(fmt_time(ignore_before)))

archivepagename = cfg['to_page'].format(datetime.now(timezone.utc).year)

for retry in range(FAIL_RETRY, 0, -1):
    mainpage = pywikibot.Page(site, cfg['from_page'])
    text = split_sections(mainpage.text)
    if args.debug:
        print('split ok')

    oldpagetext = text[0]
    newpagetext = {}
    archive_count = 0

    for i in range(1, SECTION_COUNT + 1):
        marker = uuid.uuid4().hex
        # Each request starts with "=== username ==="
        requests = re.sub(REQUEST_REGEX, marker + r'\1', text[i], flags=re.M).split(marker)
        if args.debug:
            print('section {} find {} revoke'.format(i, len(requests) - 1))
        oldpagetext += requests[0]

        for temp in requests[1:]:
            username = re.match(r'===\s*(.+?)\s*===', temp).group(1)
            m = re.search(r'{{\s*[Ss]tatus\s*\|\s*(.+?)\s*(\||}})', temp)
            status = m.group(1) if m else None
            if args.debug:
                print(username, 'status', status, sep='\t', end='\t')

            if status not in cfg['statusdone']:
                if args.debug:
                    print('pass')
                oldpagetext += temp
                continue

            lasttime = 0
            for m in re.findall(TIME_REGEX, temp):
                t = converttime(m)
                if t < ignore_before:
                    continue
                lasttime = max(lasttime, t)
            if args.debug:
                print('lasttime=' + fmt_time(lasttime), end='\t')

            if time.time() - lasttime > retention_time[i]:
                if args.debug:
                    print('archive')
                newpagetext.setdefault(i, []).append(temp)
                archive_count += 1
            else:
                if args.debug:
                    print('not archive')
                oldpagetext += temp

    if archive_count == 0:
        print('no change')
        exit()

    summary = cfg['from_page_summary'].format(archive_count)
    print('edit {} summary={}'.format(cfg['from_page'], summary))
    try:
        save_page(mainpage, oldpagetext, summary)
        break
    except EditConflictError:
        print('edit conflict')
        if retry == 1:
            print('quit')
            exit()
        print('retry')

for retry in range(FAIL_RETRY, 0, -1):
    archivepage = pywikibot.Page(site, archivepagename)
    if archivepage.exists():
        print('{} exist'.format(archivepagename))
        text = archivepage.text
    else:
        print('{} not exist'.format(archivepagename))
        text = '{{存档页|' + cfg['from_page'] + '}}\n'
        for i in range(1, SECTION_COUNT + 1):
            text += '== ' + cfg['text{}'.format(i)] + ' ==\n'

    text = split_sections(text)
    if args.debug:
        print('split ok')

    for section, newtext in newpagetext.items():
        text[section] += '\n' + '\n'.join(newtext)

    text = '\n'.join(text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    summary = cfg['to_page_summary'].format(archive_count)
    print('edit {} summary={}'.format(archivepagename, summary))
    try:
        save_page(archivepage, text, summary)
        break
    except EditConflictError:
        print('edit conflict')
        if retry == 1:
            print('quit')
            exit()
        print('retry')
print('done')
