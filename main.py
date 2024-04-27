import re
import utils
import yt_dlp
import jiocine
import subprocess

from urllib import parse
from cdm.devices import devices
from cdm.wvdecrypt import WvDecrypt
from base64 import b64decode, b64encode
from yt_dlp.postprocessor import PostProcessor
from utils import scriptsDir, joinPath, realPath

# Generate main config file from definition config before starting
configPath = joinPath(scriptsDir, 'config.json')
if not utils.isExist(configPath):
    utils.copyFile(joinPath(scriptsDir, 'config.def'), configPath)

# Some important variables
default_res = -1
default_strm = ''
config = utils.JSO(configPath, 4)


# Check Multi lang support
def multi_lang(_content_data):
    if "assetsByLanguage" in _content_data and len(_content_data["assetsByLanguage"]) > 0:
        other_langs = []

        for _lang in _content_data["assetsByLanguage"]:
            if _lang['id'] in jiocine.LANG_MAP:
                other_langs.append({
                    'id': _lang['id'],
                    'name': jiocine.LANG_MAP[_lang['id']],
                    'assetsId': _lang['assetId']
                })

        print('[=>] Multiple Languages Found:')
        for _idx, _lang in enumerate(other_langs):
            print(f'[{_idx + 1}] {_lang["name"]}')

        asset_idx = input(f'[?] Which language you want to choose(Default: {_content_data["defaultLanguage"]})?: ')
        if len(asset_idx) < 1:
            asset_idx = 1
        asset_idx = int(asset_idx) - 1
        if asset_idx < 0 or asset_idx >= len(other_langs):
            print("[!] Unknown Language Choice")
            exit(0)

        return other_langs[asset_idx]

    # Default language
    def_lang = _content_data["defaultLanguage"]
    return {
        'id': jiocine.REV_LANG_MAP[def_lang],
        'name': def_lang,
        'assetsId': _content_data['id'],
    }


# Fetch Widevine keys using PSSH
def fetch_widevine_keys(pssh_kid_map, content_playback, playback_data):
    got_cert = False
    cert_data = None
    pssh_cache = config.get("psshCacheStore")

    # Get Keys for all KIDs of PSSH
    for pssh in pssh_kid_map.keys():
        print(f'[*] PSSH: {pssh}')

        # Need to fetch even if one key missing
        fetch_keys = False
        if pssh in pssh_cache:
            for kid in pssh_cache[pssh].keys():
                if kid not in pssh_kid_map[pssh]:
                    fetch_keys = True
                    break
        else:
            fetch_keys = True

        if fetch_keys:
            # Fetch License Certificate of not Present
            if not got_cert:
                print(f'[=>] Get Widevine Server License')
                cert_req = b64decode("CAQ=")
                cert_data = jiocine.getWidevineLicense(playback_data["licenseurl"], cert_req,
                                                       config.get("authToken"), content_playback["playbackId"])
                cert_data = b64encode(cert_data).decode()
                got_cert = True

            print(f'[=>] Perform Widevine Handshake for Keys')

            wv_decrypt = WvDecrypt(devices.device_samsung_sm_g935f, cert_data)

            challenge = wv_decrypt.get_challenge(pssh)

            wv_license = jiocine.getWidevineLicense(playback_data["licenseurl"], challenge,
                                                    config.get("authToken"), content_playback["playbackId"])

            wv_decrypt.update_license(wv_license)

            # Add keys to the map
            pssh_cache[pssh] = wv_decrypt.get_keys()

            # Flush to new Cache
            config.set("psshCacheStore", pssh_cache)


# Use mp4decrypt to decrypt vod(video on demand) using kid:key
def decrypt_vod_mp4d(kid, key, input_path, output_path):
    # Create mp4decrypt command
    mp4decPath = realPath(joinPath(scriptsDir, config.get('mp4decPath')))
    command = [mp4decPath, '--key', f"{kid}:{key}", input_path, output_path]
    process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)
    for line in process.stderr:
        print(line)
    process.communicate()


# Use ffmpeg to merge video and audio
def merge_vod_ffmpeg(in_video, in_audio, output_path):
    # Create ffmpeg command
    ffmpegPath = realPath(joinPath(scriptsDir, config.get('ffmpegPath')))
    command = [ffmpegPath, '-hide_banner', '-i', in_video, '-i', in_audio, '-c:v', 'copy', '-c:a', 'copy', output_path]
    process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)
    for line in process.stderr:
        print(line)
    process.communicate()


# Use yt-dlp to download vod(video on demand) as m3u8 or dash streams into a video file
def download_vod_ytdlp(url, content, has_drm=False, rid_map=None):
    global default_res

    # Conversion Map for Type to Sub Folder
    sub_dir = jiocine.contentTypeDir[content["mediaType"]]
    output_dir_name = f'{content["fullTitle"]} ({content["releaseYear"]})'

    is_series_episode = content["mediaType"] == "EPISODE"
    if is_series_episode:
        output_dir_name = f'{content["seasonName"]} ({content["releaseYear"]})'

    # Output dir path
    output_dir = config.get('downloadPath').format(sub_dir, output_dir_name)
    output_dir = realPath(joinPath(scriptsDir, output_dir))
    temp_dir = realPath(joinPath(scriptsDir, config.get('tempPath')))
    ffmpegPath = realPath(joinPath(scriptsDir, config.get('ffmpegPath')))

    # Separate out baseUrl and Query
    parsed_url = parse.urlparse(url)
    base_url = url.replace(parsed_url.query, '')[:-1]
    query_head = parsed_url.query.replace("=", ":", 1).split(":")

    # Add more Headers
    ydl_headers = {
        query_head[0]: query_head[1]
    }
    ydl_headers.update(jiocine.headers)

    ydl_opts = {
        'no_warnings': True,
        'nocheckcertificate': True,
        'format': 'bv+ba/b',
        'paths': {
            'home': output_dir,
            'temp': temp_dir
        },
        'outtmpl': {
            'default': f'{output_dir_name}.%(ext)s',
        },
        'ffmpeg_location': ffmpegPath,
        'http_headers': ydl_headers
    }

    # yt-dlp can't download or merge if DRM is present
    if has_drm:
        ydl_opts['allow_unplayable_formats'] = True

    try:
        content_info = yt_dlp.YoutubeDL(ydl_opts).extract_info(base_url, download=False)
    except yt_dlp.utils.DownloadError as e:
        print(f"[!] Error Fetching Content Info: {e}")
        return

    # Save Resolution Choice for every episode
    if default_res < 0:
        # Video Resolution Choose
        vid_res = []
        for _format in content_info["formats"]:
            if 'height' in _format and _format['height']:
                vid_res.append(_format['height'])

        if len(vid_res) > 0:
            vid_res.reverse()

            print('[=>] Video Resolutions:')
            for _idx, reso in enumerate(vid_res):
                print(f'[{_idx + 1}] {reso}p')

            reso_idx = input(f'[?] Choose Resolutions?: ')
            if len(reso_idx) > 0:
                reso_idx = int(reso_idx) - 1
                if 0 <= reso_idx < len(vid_res):
                    ydl_opts['format'] = f"bv[height={vid_res[reso_idx]}]+ba/b[height={vid_res[reso_idx]}]"
                    default_res = vid_res[reso_idx]
                else:
                    print("[!] Unknown Choice, Going with Default")
            else:
                print("[!] Unknown Choice, Going with Default")
    else:
        ydl_opts['format'] = f"bv[height={default_res}]+ba/b[height={default_res}]"

    # Update output name
    output_name = output_dir_name.replace(" ", "_")
    if is_series_episode:
        output_name = f'E{content["episode"]}-{content["fullTitle"]}'
        print(f'[=>] Downloading S{content["season"]}E{content["episode"]} {content["fullTitle"]}')
    else:
        print(f"[=>] Downloading {output_dir_name}")
    output_name += f'.{content_info["height"]}p'
    output_name += f'.{content["defaultLanguage"]}'
    output_name += '.WEB-DL'

    # Audio Codec
    if 'acodec' in content_info:
        acodec = content_info['acodec']
        if acodec and acodec in jiocine.AUDIO_CODECS:
            acodec = jiocine.AUDIO_CODECS[acodec]
            if 'AAC' in acodec:
                output_name += '.AAC'
            elif 'AC3' in acodec:
                output_name += '.DD'
            elif 'EAC3' in acodec:
                output_name += '.DD+'

    # Video Codec
    dyr = content_info['dynamic_range']
    if dyr and dyr == 'HDR':
        output_name += '.x265.10bit.HDR'
    else:
        vcodec = content_info['vcodec']
        if vcodec and 'hvc' in vcodec:
            output_name += '.x265'
        else:
            output_name += '.x264'

    output_name += '.%(ext)s'

    ydl_opts['outtmpl']['default'] = output_name

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Custom Decryter for DRM Vids
            if has_drm:
                class DRMDecryptPP(PostProcessor):
                    def run(self, info):
                        # If hls stream
                        if 'requested_formats' not in info:
                            return [], info

                        # If decrypted file already there
                        if 'filepath' not in info['requested_formats'][0]:
                            return [], info

                        del_paths = []
                        dec_paths = []
                        self.to_screen('Doing Post Processing')
                        pssh_cache = config.get("psshCacheStore")

                        # Try finding key for
                        for fmts in info['requested_formats']:
                            fmt_id = fmts['format_id']
                            filepath = fmts['filepath']

                            fmt_code = f"f{fmt_id}"
                            outPath = fmts['filepath'].replace(fmt_code, fmt_code + "dec")

                            if fmt_id in rid_map:
                                _data = rid_map[fmt_id]
                                pssh = _data['pssh']
                                kid = _data['kid']

                                if pssh in pssh_cache:
                                    _data = pssh_cache[pssh]
                                    self.to_screen(f'{kid}:{_data[kid]}')
                                    self.to_screen('Decrypting Content')
                                    decrypt_vod_mp4d(kid, _data[kid], filepath, outPath)
                                    del_paths.append(filepath)
                                    dec_paths.append(outPath)

                        # Merge both decrypted parts
                        self.to_screen('Merging Audio and Video')
                        merge_vod_ffmpeg(dec_paths[0], dec_paths[1], info['filepath'])

                        # Delete temp files
                        del_paths.extend(dec_paths)

                        # Move final Video to Out Dir
                        info['__files_to_move'] = {
                            info['filepath']: None
                        }

                        self.to_screen('Completed Post Processing')
                        return del_paths, info

                ydl.add_post_processor(DRMDecryptPP(), when='post_process')

            ydl.download([base_url])
    except yt_dlp.utils.DownloadError as e:
        print(f"[!] Error Downloading Content: {e}")


def download_playback(_content_id, _content_data):
    global default_strm

    print(f'[=>] Fetching Playback Details')
    content_playback = jiocine.fetchPlaybackData(_content_id, config.get("authToken"))
    if not content_playback:
        print("[X] Playback Details Not Found!")
        exit(0)

    # Display Content Info
    # print(f'[*] Id: {content_playback["contentId"]}')
    # print(f'[*] Name: {content_playback["fullTitle"]}')
    # print(f'[*] Type: {content_playback["contentType"]}')
    # print(f'[*] Language: {content_playback["defaultLanguage"]}')
    # print(f'[*] Total Duration: {content_playback["totalDuration"]}')

    playback_data = None
    playback_urls = content_playback["playbackUrls"]

    # Choose Playback Url
    n_playbacks = len(playback_urls)
    if n_playbacks > 1:
        # Save Stream Type Choice for every episode
        if len(default_strm) < 1:
            strm_type = input('[?] Which Stream Type HLS or DASH?: ')
            if any(strm_type.lower() == f for f in ['hls', 'dash']):
                default_strm = strm_type.lower()
                for data in playback_urls:
                    if data['streamtype'] == default_strm:
                        playback_data = data
                        break
            else:
                print("[X] Unknown Choice, Selecting First!")
                playback_data = playback_urls[0]
        else:
            for data in playback_urls:
                if data['streamtype'] == default_strm:
                    playback_data = data
                    break
    elif n_playbacks == 1:
        playback_data = playback_urls[0]

    if not playback_data:
        print("[X] Unable to get Playback Url!")
        exit(0)

    print(f'[*] URL: {playback_data["url"]}')
    print(f'[*] Encryption: {playback_data["encryption"]}')
    print(f'[*] Stream Type: {playback_data["streamtype"]}')

    # Handle Widevine Streams
    if playback_data["streamtype"] == "dash":
        # Download MPD manifest for PSSH
        print(f'[=>] Getting MPD manifest data')

        mpd_data = jiocine.getMPDData(playback_data["url"])
        if not mpd_data:
            print("[!] Failed to get MPD manifest")
            exit(0)

        periods = mpd_data['MPD']['Period']
        if not periods:
            print("[!] Failed to parse MPD manifest")
            exit(0)

        rid_kid, pssh_kid = jiocine.parseMPDData(periods)

        # Proceed for DRM keys only if PSSH is there
        if len(pssh_kid) > 0:
            # Get the Decryption Keys into cache
            fetch_widevine_keys(pssh_kid, content_playback, playback_data)

            # Download Audio, Video streams
            download_vod_ytdlp(playback_data['url'], _content_data, has_drm=True, rid_map=rid_kid)
        else:
            print("[!] Can't find PSSH, Content may be Encrypted")
            download_vod_ytdlp(playback_data['url'], _content_data)
    elif playback_data["streamtype"] == "hls" and playback_data["encryption"] == "aes128":
        download_vod_ytdlp(playback_data['url'], _content_data)
    else:
        print("[X] Unsupported Stream Type!")


if __name__ == '__main__':
    print('[=>] Jio Cinema Downloader Starting')

    # Fetch Guest token when Not using Account token
    if not config.get("authToken") and not config.get("useAccount"):
        print("[=>] Guest Token is Missing, Requesting One")
        guestToken = jiocine.fetchGuestToken()
        if not guestToken:
            print("[!] Guest Token Not Received")
            exit(0)

        print("[=>] Got Guest Token :)")
        config.set("authToken", guestToken)

    print(f'[=>] Welcome {config.get("accountName")}, Jio Cinema Free User')

    # content_id = input(f'[?] Enter Content Id: ')
    # if len(content_id) < 1:
    #     print("[!] Enter Valid Id")
    #     exit(0)

    content_url = input(f'[?] Enter Content Url: ')
    if len(content_url) < 1:
        print("[!] Enter Valid Url")
        exit(0)

    # Ref: https://stackoverflow.com/questions/7160737/python-how-to-validate-a-url-in-python-malformed-or-not
    # URL Sanitization
    urlRegex = re.compile(
        r'^(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)

    # URL Check
    if re.match(urlRegex, content_url) is None:
        print("Please Provide Valid URL!")
        exit(0)

    # Get and validate content id
    content_url = content_url.split('/')
    try:
        int(content_url[-1])
        content_id = content_url[-1]
    except:
        print("Please Provide Valid URL!!")
        exit(0)

    print('[=>] Fetching Content Details')
    # content_id = 3216132  # 3760812  # 4K Test: 3719559
    content_data = jiocine.getContentDetails(content_id)
    if not content_data:
        print("[X] Content Details Not Found!")
        exit(0)

    print('[=>] Found Video Details')
    print(f'[*] Id: {content_data["id"]}')
    print(f'[*] Name: {content_data["shortTitle"]}')
    print(f'[*] Type: {content_data["mediaType"]}')
    print(f'[*] Default Language: {content_data["defaultLanguage"]}')
    print(f'[*] Release Year: {content_data["releaseYear"]}')

    answer = input('[?] Do you want to continue (yes/no)?: ')
    if any(answer.lower() == f for f in ['no', 'n']):
        print("[!] Downloader Stopped")
        exit(0)

    if content_data['isPremium'] and not config.get("hasPremium"):
        print("[!] Need Premium Account for this Content")
        exit(0)

    # Show and Series links are complicated
    if content_data["mediaType"] == "SHOW" or content_data["mediaType"] == "SERIES":
        print("[!] Shows/Series Link Unsupported, Download Using Individual Episodes Links")
        exit(0)

    # There may be other languages
    lang_data = multi_lang(content_data)
    if lang_data and content_data != lang_data['assetsId']:
        print('[=>] Language Changed!')
        print(f'[*] Id: {lang_data["id"]}')
        print(f'[*] Language: {lang_data["name"]}')

        # Update Content Details
        content_id = lang_data['assetsId']
        content_data = jiocine.getContentDetails(content_id)
        if not content_data:
            print("[X] Content Details Not Found!")
            exit(0)

    # Give Full Series a Chance ;)
    if content_data["mediaType"] == "EPISODE" and len(content_data["seasonId"]) > 0:
        need_series = input('[?] Do you want to download whole series (yes/no)?: ')
        if any(need_series.lower() == f for f in ['yes', 'y']):
            season_id = content_data['seasonId']

            season_data = jiocine.getContentDetails(season_id)
            if not season_data:
                print("[X] Season Details Not Found!")
                exit(0)

            print('[=>] Found Season Details')
            print(f'[*] Name: {season_data["shortTitle"]}')
            print(f'[*] Type: {season_data["mediaType"]}')
            print(f'[*] Default Language: {season_data["defaultLanguage"]}')
            print(f'[*] Release Year: {season_data["releaseYear"]}')

            episodes = jiocine.getSeriesEpisodes(season_id)
            if not episodes:
                print("[X] Season Episodes Not Found!")
                exit(0)

            # Go through every episode with language choice
            for idx, episode in enumerate(episodes):
                episode_id = episode['id']

                episode_data = jiocine.getContentDetails(episode_id)
                if not episode_data:
                    print(f"[X] Episode-{idx + 1} Details Not Found!")
                    continue

                # Find Chosen Language
                if "assetsByLanguage" in episode_data and len(episode_data["assetsByLanguage"]) > 0:
                    for lang in episode_data["assetsByLanguage"]:
                        if lang_data["id"] == lang['id']:
                            # Change Language
                            episode_id = lang['assetId']
                            episode_data = jiocine.getContentDetails(episode_id)
                            if not episode_data:
                                print(f"[X] Episode-{idx + 1} Details Not Found!!")
                                continue
                            break

                # Download Each Episode of Season
                download_playback(episode_id, episode_data)
        else:
            # Download Single Episode Only
            download_playback(content_id, content_data)
    else:
        # Download Single Episode or Movie
        download_playback(content_id, content_data)

    print("[=>] Jio Cinema Downloader Complete")
