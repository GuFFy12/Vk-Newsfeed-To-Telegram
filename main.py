import configparser
import json
import os
import sys
import threading
import time

import telebot
import vk_api
from telebot import types


config = configparser.ConfigParser()
config.read("settings.ini")

vk_login = config["VK"]["login"]
vk_password = config["VK"]["password"]
telegram_token = config["Telegram"]["token"]
telegram_chat = config["Telegram"]["chat"]
time_check = int(config["Settings"]["time_check"])
retries_max = int(config["Settings"]["retries_max"])
retries_time = int(config["Settings"]["retries_time"])

module = sys.modules[__name__]
if os.path.isfile('latest.log'):
    os.remove('latest.log')


def logger(log):
    log = time.strftime(f'[%H:%M:%S] {log}')
    print(log)
    with open('latest.log', 'a', encoding='utf-8') as f:
        f.write(f'{log}\n')


def captcha_handler(captcha):
    key = input('Enter Captcha {0}: '.format(captcha.get_url())).strip()
    return captcha.try_again(key)


def auth_handler():
    key = input('Enter authentication code: ')
    remember_device = True
    return key, remember_device


def init_telegram():
    module.bot = telebot.TeleBot(telegram_token)
    logger('Successfully logged in in telegram!')


def init_vk():
    vk_session = vk_api.VkApi(
        login=vk_login,
        password=vk_password,
        auth_handler=auth_handler,
        captcha_handler=captcha_handler,
    )
    module.vk = vk_session.get_api()

    try:
        vk_session.auth()
        logger('Successfully logged in in VK!')
    except vk_api.AuthError as e:
        logger('VK: ' + str(e))

    checker(int(time.time()))


def checker(start_time):
    while True:
        time.sleep(time_check)

        newsfeed = module.vk.newsfeed.get(
            count=100, start_time=start_time, max_photos=10
        )
        posts = (json.loads(json.dumps(newsfeed))).get('items')

        if posts:
            start_time = posts[0]['date'] + 1
            logger('New posts was founded!')
            for post in posts[::-1]:
                check_attachments(post)


def check_attachments(post):
    if post.get('photos'):
        return

    if post.get('copy_history'):
        post = post['copy_history'][0]

    if not (post.get('attachments')):
        logger('Post without attachments.')
    else:
        logger('From post...')
        transfer_attachments_to_telegram(get_attachments(post))


def get_sizes(size_path):
    photo_size = None

    for photoType in size_path[0:]:
        if photoType.get('type') == 'x':
            photo_size = photoType.get('url')
        if photoType.get('type') == 'y':
            photo_size = photoType.get('url')
        if photoType.get('type') == 'z':
            photo_size = photoType.get('url')
        if photoType.get('type') == 'w':
            photo_size = photoType.get('url')

    return photo_size


def get_attachments(post):
    attach_list = []
    photo_group = []

    for att in post['attachments'][0:]:
        att_type = att.get('type')
        attachment = att[att_type]

        attachments = None
        title = None
        preview = None

        if att_type == 'photo':
            photo_size = get_sizes(attachment.get('sizes'))

            photo_group.append(photo_size)
            continue

        elif att_type == 'video':
            retries = 0
            photos = {}

            owner_id = str(attachment.get('owner_id'))
            video_id = str(attachment.get('id'))
            access_key = str(attachment.get('access_key'))

            for key, value in attachment.items():
                if key.startswith('photo_'):
                    photos[key] = value

            preview = attachment[max(photos)]
            title = attachment.get('title')
            full_url = str(owner_id + '_' + video_id + '_' + access_key)

            while retries_max > retries:
                attachments = module.vk.video.get(videos=full_url)['items'][0].get('player')

                if attachments is not None:
                    break
                else:
                    retries += 1
                    logger(f'VK did not process the video. Retry {retries}/{retries_max}...')
                    time.sleep(retries_time)
                    continue

            else:
                logger(f'Unable to get video link after {retries_max} retries.')

        elif att_type == 'doc':
            title = attachment.get('title')

            doc_type = attachment.get('type')
            if doc_type != 3 and doc_type != 4 and doc_type != 5:
                att_type = 'other'

            attachments = attachment.get('url')

        elif att_type == 'album':
            preview = get_sizes(attachment['thumb'].get('sizes'))
            title = attachment.get('title')

            owner_id = str(attachment.get('owner_id'))
            album_id = str(attachment.get('id'))

            attachments = str(f'https://vk.com/album{owner_id}_{album_id}')

        elif att_type == 'link' and attachment.get('description') == 'Статья':
            preview = get_sizes(attachment['photo'].get('sizes'))
            title = attachment.get('title')

            attachments = str(attachment.get('url'))

        if attachments is not None:
            attach_list.append({'type': att_type, 'link': attachments, 'title': title, 'preview': preview})
        else:
            logger(f'Undefined type of attachment: {att_type}')
            logger(attachment)

    if photo_group:
        attach_list.append({'type': 'photo', 'link': photo_group})

    return attach_list


def transfer_attachments_to_telegram(attachments):

    for attach_element in attachments[0:]:
        retries = 0

        att_type = attach_element.get('type')
        link = attach_element.get('link')
        title = attach_element.get('title')
        preview = attach_element.get('preview')

        while retries_max > retries:
            try:
                if att_type == 'photo':
                    media_photo = []

                    for photo_url in link[0:]:
                        media_photo.append(types.InputMediaPhoto(photo_url))

                    module.bot.send_media_group(telegram_chat, media_photo)
                    logger('Send photo group.')

                elif att_type == 'video' or att_type == 'album' or att_type == 'link':
                    module.bot.send_media_group(
                        telegram_chat,
                        [types.InputMediaPhoto(preview, caption=f'{title}\n{link}')],
                    )
                    logger(f'Send {att_type} group.')

                elif att_type == 'doc' or att_type == 'gif':
                    module.bot.send_document(telegram_chat, link)
                    logger('Send document group.')

                elif att_type == 'other':
                    module.bot.send_message(telegram_chat, f'{title}\n{link}')
                    logger('Send other group.')

                break

            except Exception as e:
                retries += 1

                if 'Too Many Requests: retry after' in str(e):
                    wait = str(e).split()[-1]
                    logger(f'[{retries}/{retries_max}] Detect telegram api timeout. Wait: {wait}s')
                    time.sleep(int(wait))
                    continue

                elif 'Bad Request: group send failed' in str(e):
                    logger(f'[{retries}/{retries_max}] Detect telegram error of group send failed.')
                    time.sleep(retries_time)
                    continue

                elif 'Read timed out.' in str(e):
                    logger(f'[{retries}/{retries_max}] Detect telegram error of read timed out.')
                    time.sleep(retries_time)
                    continue

                elif 'Bad Request: failed to get HTTP URL content' in str(e):
                    logger(f'[{retries}/{retries_max}] Detect telegram error to get URL content, maybe it too heavy...')
                    att_type = 'other'
                    continue

                else:
                    logger(f'[{retries}/{retries_max}] {e}')
                    logger(attachments)
                    continue

        else:
            logger(f'Unable to send attachment after {retries_max} retries.')


t1 = threading.Thread(target=init_vk)
t2 = threading.Thread(target=init_telegram)

t1.start()
t2.start()
t1.join()
t2.join()
