#!/usr/bin/env python3
import json
import threading
import requests
import time
import os
import datetime
import subprocess
import psutil
import signal
import re
import yt_dlp
import logging
import jsonconv
import optparse

threads = []
unload = False

def ntfy(title, text, url = ''):
    # https://ntfy.sh/docs
    try:
        requests.post(
            "https://ntfy.sh/" + options.ntfy_id,
            data = text.encode('utf-8'),
            headers = {
                "title": title,
                "click": url
            }
        )
    except Exception as ex:
        if 'Temporary failure in name resolution' not in str(ex):
            time.sleep(5)

def date_time(format):
    return datetime.datetime.now().strftime(format)

def str_cut(string, letters, postfix='...'):
    return string[:letters] + (string[letters:] and postfix)

def str_fix(string):
    return str_cut(re.sub(r'[/\\?%*:|"<>]', '', string), 100, '')

def dump_stream(input_dict):
    def dump_stream_json(url):
        ytdlp_config = {
            'quiet': True,
            'playlist_items': 0,
            'noplaylist': True
        }

        try:
            with yt_dlp.YoutubeDL(ytdlp_config) as ydlp:
                return ydlp.extract_info(url, download=False)

        except Exception as ex:
            if 'Temporary failure in name resolution' in str(ex):
                time.sleep(5)

    def dump_thumb(dir, video_id):
        hq_blank = "https://i.ytimg.com/vi/%s/hqdefault.jpg"
        max_blank = "https://i.ytimg.com/vi/%s/maxresdefault.jpg"

        try:
            for blank in [hq_blank, max_blank]:
                with requests.get(blank % video_id, stream=True) as request:
                    if request:
                        with open(dir, 'wb') as file:
                            file.write(request.content)
            return
        except Exception as ex:
            if 'Temporary failure in name resolution' in str(ex):
                time.sleep(5)

    start_time = time.time()
    stream_json = dump_stream_json(input_dict['url'])

    if stream_json['extractor'] == "youtube":
        url_title = str_fix(stream_json['title'][:-17])
        url_name = str_fix(stream_json['uploader'])

    elif stream_json['extractor'] == "twitch:stream":
        url_title = str_fix(stream_json['description'])
        url_name = str_fix(stream_json['uploader'])

    elif stream_json['extractor'] == "wasdtv:stream":
        url_title = str_fix(stream_json['fulltitle'])
        url_name = str_fix(stream_json['webpage_url_basename'])

    else:
        url_title = str_fix(stream_json['title'])
        url_name = str_fix(stream_json['uploader'])

    if input_dict['regex']:
        regex = input_dict['regex'].lower()

        re_title = re.findall(regex, url_title.lower())
        re_desc = re.findall(regex, stream_json['description'].lower())

        if not re_title and not re_desc:
            return

    file_title = f'[{date_time("%y-%m-%d %H_%M_%S")}] {url_name} - {url_title}'
    file_dir = f"{options.output}/[live] {file_title.rstrip()}"

    os.makedirs(file_dir, exist_ok=True)

    # dump preview image
    if 'youtube' in stream_json['extractor']:
        dump_thumb(f"{file_dir}/{file_title}.jpg", stream_json['id'])

    # saving stream info
    with open(f"{file_dir}/{file_title}.info", 'w') as info:
        info.write( json.dumps(stream_json, indent=10, ensure_ascii=False, sort_keys=True) )

    # notify
    logging.info(f'[online] ({url_name} - {url_title})')
    ntfy(f'{url_name} is online.', f'{url_title}', stream_json['webpage_url'])

    _comm_stream = [
        "streamlink",
        "--output", f"{file_dir}/{file_title}.ts",
        "--url", input_dict['url'],
        "--fs-safe-rules", "Windows",
        "--twitch-disable-ads",
        "--http-timeout", "180",
        "--hls-live-restart",
        "--stream-segment-threads", "2",
        "--stream-segment-timeout", "180",
        "--stream-segment-attempts", "300",
        "--hls-segment-ignore-names", "preloading",
        "--hls-playlist-reload-attempts", "30",
        "--hls-live-edge", "5",
        "--stream-timeout", "120",
        "--ringbuffer-size", "64M",
        "--loglevel", "trace",
        "--default-stream", input_dict['quality'],
        "--twitch-disable-hosting"
    ]

    if options.force_ytdlp:
        # install ffmpeg!!!   
        _comm_stream = [
            "yt-dlp",
            input_dict['url'],
            "--ignore-config",
            "--hls-use-mpegts",
            "--no-part",
            "--retries", "30",
            "--verbose",
            "-o", f"{file_dir}/{file_title}.ts"
        ]

    if options.force_ytarchive and stream_json['extractor'] == "youtube":
        # install ffmpeg!!!
        _comm_stream = [
            "ytarchive",
            "--debug", 
            "--trace", 
            "--verbose",
            "--threads", "4",
            "--add-metadata",
            "--no-merge", # for slow drives
            "-o", f"{file_dir}/{file_title}.mp4",
            "--no-frag-files",
            input_dict['url'],
            input_dict['quality']
        ]

    _comm_chat = [
        'chat_downloader',
        stream_json['webpage_url'],
        "--output", f"{file_dir}/{file_title}.json",
        "--inactivity_timeout", "99999999"
    ]

    # ext stream dump process
    txt_stream = open(f"{file_dir}/{file_title}.log", "w")
    process_stream = subprocess.Popen(_comm_stream, stdout=txt_stream, stderr=txt_stream)
    pid_stream = psutil.Process(process_stream.pid)

    # chat saving (need 'chat_downloader' pkg for this)
    txt_chat = open(f"{file_dir}/{file_title}.chat", "w")
    process_chat = subprocess.Popen(_comm_chat, stdout=txt_chat, stderr=txt_chat)
    pid_chat = psutil.Process(process_chat.pid)

    # thread loop until stream ending
    threads.append(input_dict['url'])

    while not unload:
        time.sleep(5)

        st_zombie = pid_stream.status() == psutil.STATUS_ZOMBIE
        st_running = pid_stream.is_running()

        if st_zombie or not st_running:
            break

    end_time = datetime.timedelta(seconds=int(f'{time.time() - start_time:.{0}f}'))
    threads.remove(input_dict['url'])

    txt_chat.close()
    txt_stream.close()

    if pid_stream.is_running():
        if unload:
            os.kill(process_stream.pid, signal.SIGTERM)
        else:
            os.waitpid(process_stream.pid, 0)
            ntfy(f'{url_name} is offline. [{end_time}]', f'{url_title}', stream_json['webpage_url'])

        logging.info(f'[offline] ({url_name} - {url_title}) ({end_time})')

    if pid_chat.is_running():
        if pid_chat.status() == psutil.STATUS_ZOMBIE:
            os.waitpid(process_chat.pid, 0)
        if pid_chat.is_running():
            os.kill(process_chat.pid, signal.SIGTERM)

    try:
        jsonconv.json2txt(f"{file_dir}/{file_title}.json")
    except:
        pass
    os.rename(file_dir, f"{options.output}/{file_title.rstrip()}")

def check_live(url):
    with subprocess.Popen(['streamlink', "--url", url, '--twitch-disable-hosting'], stdout=subprocess.PIPE) as proc:
        while True:
            if proc.poll() == None:
                time.sleep(0.1)
            elif proc.poll() == 0:
                return True
            else:
                return False

def dump_list(input):
    _list = {}
    with open(input) as file:
        for line in file:
            line = line.rstrip()
            if len(line) > 0 and line[0] != '#':
                split = line.split()
                url = split[0]
                quality = "best"
                regex = ""

                if len(split) > 1:
                    quality = split[1]

                if len(split) > 2:
                    regex = split[2]

                if url.find('youtube') != -1 and url.find('watch?v=') == -1:
                    url += '/live'

                _list[len(_list)] = {
                    'url': url,
                    'regex': regex,
                    'quality': quality
                }

    return _list

if __name__ == "__main__":
    parser = optparse.OptionParser()
    parser.add_option('-o', '--output', dest='output', default='.', help='Streams dest')
    parser.add_option('-d', '--delay', dest='delay_check', type=int, default='10', help='Streams check delay')
    parser.add_option('-s', '--src', dest='src_name', default='list.txt', help='File with channels/streams')
    parser.add_option('-l', '--log', dest='log_name', default='log.txt', help='Log file')
    parser.add_option('-f', '--ntfy', dest='ntfy_id', help='ntfy.sh channel')
    parser.add_option('-y', '--yt-dlp', dest='force_ytdlp', action='store_true', help='use yt-dlp instead of streamlink')
    parser.add_option('-a', '--ytarchive', dest='force_ytarchive', action='store_true', help='use ytarchive for yt streams')
    options, arguments = parser.parse_args()

    if options.output != '.':
        os.makedirs(options.output, exist_ok=True)

    logging.basicConfig(
        format='%(asctime)s | %(message)s',
        datefmt='%y.%m.%d %H:%M:%S',
        level=logging.INFO,
        handlers=[
            logging.FileHandler(options.log_name),
            logging.StreamHandler()
        ]
    )

    logging.info('Starting...')
    try:
        while True:
            urls = dump_list(options.src_name)

            for i in range(len(urls)):
                if urls[i]['url'] not in threads and check_live(urls[i]['url']):
                    threading.Thread(target=dump_stream, args=(urls[i], )).start()
                else:
                    print(f'({i + 1} / {len(urls)}) {len(threads)} is streaming.', end='\r')
                time.sleep(options.delay_check)

    except KeyboardInterrupt:
        if threads:
            unload = False
            logging.info('Stopping...')
            while threading.active_count() > 1:
                time.sleep(0.1)
