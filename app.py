"""
******************************************************************

        File name   : webapi.py
        Description : Flask based API to work with webwhatsapi
                      Called from wsgi.py. Can be run as a
                      standalone file to (cmd: python WebAPI.py)

                      The API use chrome as of now, can be changed
                      to use firefox too

                      The API provides a way to run multiple clients
                      by the use of client_id. The api stores drivers
                      and use them in later calls

                      You need to fist call [PUT] /client to create
                      driver for that client and then you can use
                      other calls

        Requirements: Mentioned in Pipfile

# Change Logs
DATE        PROGRAMMER      COMMENT
18/09/18    rbnishant       Initial Version

*****************************************************************/
"""

import json
import logging
import os
import shutil
import requests
import sys
import time
import threading
import werkzeug

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from flask import Flask, send_file, request, abort, g, jsonify, session
from flask.json import JSONEncoder
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from selenium.common.exceptions import WebDriverException, NoSuchElementException
from werkzeug.utils import secure_filename
from webwhatsapi import MessageGroup, WhatsAPIDriver, WhatsAPIDriverStatus
from webwhatsapi.objects.whatsapp_object import WhatsappObject

"""
###########################
##### CLASS DEFINITION ####
###########################
"""


class RepeatedTimer(object):
    """
    A generic class that creates a timer of specified interval and calls the
    given function after that interval
    """

    def __init__(self, interval, function, *args, **kwargs):
        """ Starts a timer of given interval
        @param self:
        @param interval: Wait time between calls
        @param function: Function object that is needed to be called
        @param *args: args to pass to the called functions
        @param *kwargs: args to pass to the called functions
        """
        self._timer = None
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        """Creates a timer and start it"""

        if not self.is_running:
            self._timer = threading.Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        """Stop the timer"""
        self._timer.cancel()
        self.is_running = False


class WhatsAPIJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, WhatsappObject):
            return obj.get_js_obj()
        if isinstance(obj, MessageGroup):
            return obj.chat
        return super(WhatsAPIJSONEncoder, self).default(obj)


class NewMessageObserver:
    def __init__(self, appId):
        self.appId = appId

    def on_message_received(self, new_messages):
        for message in new_messages:
            logger.info("New Message event" + message)
            if message.chat_id.endswith("@c.us"):
                if message.type == "chat" or message.type == "location":
                    body = reformat_message_r2mp(message, self.appId)
                    forward_message_to_r2mp(body)
                    print(
                        "New {} message '{}' received from number {}".format(self.appId,
                                                                             message.content, message.sender.id
                                                                             )
                    )
                else:
                    print(
                        "New message of type '{}' received from number {}".format(
                            message.type, message.sender.id
                        )
                    )


"""
###########################
##### GLOBAL VARIABLES ####
###########################
"""

# Flask Application
app = Flask(__name__)
app.json_encoder = WhatsAPIJSONEncoder
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s  %(levelname)s : %(message)s', )


logger = logging.getLogger("WhatsApp Backend")
handler = logging.FileHandler('whatsapp_development.log')
formatter = logging.Formatter('%(asctime)s  %(levelname)s : %(message)s')
handler.setFormatter(formatter)
# handler.setLevel(logging.INFO)
logger.addHandler(handler)


app.debug = True

# Logger

# Driver store all the instances of webdriver for each of the client user
drivers = dict()
# Store all timer objects for each client user
timers = dict()
# Store list of semaphores
semaphores = dict()

# store quick replies payload
payload = dict()

SANDBOX_URL = "http://r2mp-sandbox.rancardmobility.com"
PRODUCTION_URL = "http://r2mp.rancard.com"
LOCAL = "http://localhost:8080"

SERVER = SANDBOX_URL

# API key needed for auth with this API, change as per usage
API_KEY = "5ohsRCA8os7xW7arVagm3O861lMZwFfl"
# File type allowed to be sent or received
ALLOWED_EXTENSIONS = (
    "avi",
    "mp4",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "mp3",
    "doc",
    "docx",
    "pdf",
)
# Path to temporarily store static files like images
STATIC_FILES_PATH = "static/"

# Seleneium Webdriver configuration
CHROME_IS_HEADLESS = True
# CHROME_CACHE_PATH = BASE_DIR + "/sample/flask/chrome_cache/"
CHROME_CACHE_PATH = BASE_DIR + "/chrome_cache/"
CHROME_DISABLE_GPU = True
CHROME_WINDOW_SIZE = "910,512"

"""
##############################
##### FUNCTION DEFINITION ####
##############################
"""


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.driver_status != WhatsAPIDriverStatus.LoggedIn:
            return jsonify({"error": "client is not logged in"})
        return f(*args, **kwargs)

    return decorated_function


def create_logger():
    """Initial the global logger variable"""
    global logger

    # formatter = logging.Formatter("%(asctime)s|%(levelname)s|%(message)s")
    # handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1)
    # handler.setFormatter(formatter)
    # handler.setLevel(log_level)
    # handler.suffix = "%Y-%m-%d"
    # logger = logging.getLogger("sacplus")
    # logger.setLevel(log_level)
    # logger.addHandler(handler)


def init_driver(client_id):
    """Initialises a new driver via webwhatsapi module

    @param client_id: ID of user client
    @return webwhatsapi object
    """

    # Create profile directory if it does not exist
    profile_path = CHROME_CACHE_PATH + str(client_id)
    if not os.path.exists(profile_path):
        os.makedirs(profile_path)

    # Options to customize chrome window
    chrome_options = [
        "window-size=" + CHROME_WINDOW_SIZE,
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Ubuntu Chromium/60.0.3112.78 Chrome/60.0.3112.78 Safari/537.36",
    ]
    if CHROME_IS_HEADLESS:
        chrome_options.append("--headless")
    if CHROME_DISABLE_GPU:
        chrome_options.append("--disable-gpu")

    # Create a whatsapidriver object
    d = WhatsAPIDriver(
        username=client_id,
        profile=profile_path,
        client="chrome",
        chrome_options=chrome_options,
    )
    return d


def init_client(client_id):
    """Initialse a driver for client and store for future reference

    @param client_id: ID of client user
    @return whebwhatsapi object
    """
    if client_id not in drivers:
        drivers[client_id] = init_driver(client_id)
    return drivers[client_id]


def delete_client(client_id, preserve_cache):
    """Delete all objects related to client

    @param client_id: ID of client user
    @param preserve_cache: Boolean, whether to delete the chrome profile folder or not
    """
    if client_id in drivers:
        drivers.pop(client_id).quit()
        try:
            timers[client_id].stop()
            timers[client_id] = None
            release_semaphore(client_id)
            semaphores[client_id] = None
        except:
            pass

    if not preserve_cache:
        pth = CHROME_CACHE_PATH + g.client_id
        shutil.rmtree(pth)


def init_timer(client_id):
    """Create a timer for the client driver to watch for events

    @param client_id: ID of clinet user
    """
    if client_id in timers and timers[client_id]:
        timers[client_id].start()
        logger.info("Previous driver timer initialised")
        return
    # Create a timer to call check_new_message function after every 2 seconds.
    # client_id param is needed to be passed to check_new_message
    logger.info("New timer started for driver")
    timers[client_id] = RepeatedTimer(2, check_new_messages, client_id)


def init_login_timer(client_id):
    """Create a timer for the client driver to watch for events

    @param client_id: ID of clinet user
    """
    timer_id = client_id + "login"
    if timer_id in timers and timers[timer_id]:
        timers[timer_id].start()
        return
    # Create a timer to call check_new_message function after every 2 seconds.
    # client_id param is needed to be passed to check_new_message
    timers[timer_id] = RepeatedTimer(3, serve_user_login, client_id)


def serve_user_login(client_id):
    """Check if user is logged in and send them to the custom api

    @param client_id: ID of client user
    """

    try:
        """ Get qr as base64 string"""

        qr = drivers[client_id].get_qr_base64()
        body = {
            'success': True,
            'appId': client_id,
            'isLoggedIn': False,
            'qr': qr
        }
        logger.info("Sending QR to server")
        response = requests.post(SERVER + '/api/v1/whatsapp/webhook', json=body)
    except NoSuchElementException:
        phone = drivers[client_id].get_id().replace("\"", "").replace("@c.us", "")
        body = {
            'success': True,
            'isLoggedIn': True,
            'appId': client_id,
            "msisdn": phone,
            "qr": None
        }
        try:
            timer_id = client_id + "login"
            timers[timer_id].stop()
            timers[timer_id] = None

            init_timer(client_id)

            logger.info("Timer killed successfully")
        except:
            logger.error("Error occurred trying to kill timer")
            pass
        response = requests.post(SERVER + '/api/v1/whatsapp/webhook', json=body)


def check_new_messages(client_id):
    """Check for new unread messages and send them to the custom api

    @param client_id: ID of client user
    """
    # Return if driver is not defined or if whatsapp is not logged in.
    # Stop the timer as well
    if (
            client_id not in drivers
            or not drivers[client_id]
            or not drivers[client_id].is_logged_in()
    ):
        timers[client_id].stop()
        return

    # Acquire a lock on thread
    if not acquire_semaphore(client_id, True):
        return

    try:
        body = {}
        # Get all unread messages
        res = drivers[client_id].get_unread()
        # Mark all of them as seen
        for message_group in res:
            message_group.chat.send_seen()
        # Release thread lock
        release_semaphore(client_id)
        # If we have new messages, do something with it
        if res:
            logger.info(res)
            # for message_group in res:
            message_group = res[0]
            if not message_group.chat._js_obj["isGroup"]:
                storage = session
                forwarder = threading.Thread(target=send_message_to_client, args=(message_group, client_id, storage))
                forwarder.start()
    except Exception as e:
        print(str(e))
        pass
    finally:
        # Release lock anyway, safekeeping
        release_semaphore(client_id)


def reformat_message_r2mp(message, appId):
    body = {"recipientMsisdn": message._js_obj["to"].replace("@c.us", ""),
            "content": message.content if message.type == "chat" else "https://www.latlong.net/c/?lat=" + str(
                message.latitude) + "&long=" + str(message.longitude)}
    # body['recipientMsisdn'] = recipient_msisdn
    if message.type == "location":
        location_url = "https://www.latlong.net/c/?lat=" + str(message.latitude) + "&long=" + str(message.longitude)
        body["content"] = '<a href="' + location_url + '" target="_blank"> Click to view location </a>'
    body['content'] = message.content
    body["type"] = "text"
    body["timeSent"] = message.timestamp.isoformat()
    body["senderMsisdn"] = message.chat_id.replace("@c.us", "")
    body["messageId"] = message.id
    body["companyId"] = appId
    body["appId"] = appId
    return body


def send_message_to_client(message_group, appId, storage):
    logger.info("Sending message to r2mp")
    # recipient_msisdn = message_group.chat.get_js_obj()['messages'][0]['to']['user']
    message = message_group.messages[0]
    if message.type == "chat" or message.type == "location":
            body = {}
            # body['recipientMsisdn'] = recipient_msisdn
            body["recipientMsisdn"] = message._js_obj["to"].replace("@c.us", "")
            body["content"] = message.content if message.type == "chat" else "https://www.latlong.net/c/?lat=" + str(
                message.latitude) + "&long=" + str(message.longitude)
            if message.type == "location":
                location_url = "https://www.latlong.net/c/?lat=" + str(message.latitude) + "&long=" + str(
                    message.longitude)
                body["content"] = '<a href="' + location_url + '" target="_blank"> Click to view location </a>'
            body['content'] = message.content
            body["type"] = "text"
            body["timeSent"] = message.timestamp.isoformat()
            body["senderMsisdn"] = message.chat_id.replace("@c.us", "")
            body['senderUsername'] = message._js_obj['sender']['pushname']
            body["messageId"] = message.id
            body["companyId"] = appId
            body["appId"] = appId

            # if its a reply
            if message._js_obj["quotedMsg"] is not None:
                text = message._js_obj['quotedMsg']['body']
                body['content'] = text
                body['postback'] = { "payload" : payload[text] }
                body['quick_reply'] = payload[text]
            forward_message_to_r2mp(body)


def forward_message_to_r2mp(message_data):
    headers = {'Content-Type': 'application/json; charset=utf-8', 'x-r2-wp-screen-name': message_data["companyId"],
               'msisdn': message_data["recipientMsisdn"]}

    response = requests.post(SERVER + "/api/v1/bot?channelType=WHATSAPP",
                             headers=headers,
                             json=message_data)
    logger.info("Message " + message_data['content'] +" sent to " + SERVER + "/api/v1/bot?channelType=WHATSAPP ---- "+ str(response))



def get_client_info(client_id):
    """Get the status of a perticular client, as to he/she is connected or not

    @param client_id: ID of client user
    @return JSON object {
        "driver_status": webdriver status
        "is_alive": if driver is active or not
        "is_logged_in": if user is logged in or not
        "is_timer": if timer is running or not
    }
    """
    if client_id not in drivers:
        return None

    driver_status = drivers[client_id].get_status()
    is_alive = False
    is_logged_in = False
    if (
            driver_status == WhatsAPIDriverStatus.NotLoggedIn
            or driver_status == WhatsAPIDriverStatus.LoggedIn
    ):
        is_alive = True
    if driver_status == WhatsAPIDriverStatus.LoggedIn:
        is_logged_in = True

    return {
        "is_alive": is_alive,
        "is_logged_in": is_logged_in,
        "is_timer": bool(timers[client_id]) and timers[client_id].is_running,
    }


def allowed_file(filename):
    """Check if file as allowed type or not

    @param filename: Name of the file to be checked
    @return boolean True or False based o#!/bin/sh

pwdn file name check
    """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def send_media(chat_id, requestObj):
    files = requestObj.files
    if not files:
        return jsonify({"Status": False})

    # create user folder if not exists
    profile_path = create_static_profile_path(g.client_id)

    file_paths = []
    for file in files:
        file = files.get(file)
        if file.filename == "":
            return {"Status": False}

        if not file or not allowed_file(file.filename):
            return {"Status": False}

        filename = secure_filename(file.filename)

        # save file
        file_path = os.path.join(profile_path, filename)
        file.save(file_path)
        file_path = os.path.join(os.getcwd(), file_path)

        file_paths.append(file_path)

    caption = requestObj.form.get("message")

    res = None
    for file_path in file_paths:
        res = g.driver.send_media(file_path, chat_id, caption)
    return res


def create_static_profile_path(client_id):
    """Create a profile path folder if not exist

    @param client_id: ID of client user
    @return string profile path
    """
    profile_path = os.path.join(STATIC_FILES_PATH, str(client_id))
    if not os.path.exists(profile_path):
        os.makedirs(profile_path)
    return profile_path


def acquire_semaphore(client_id, cancel_if_locked=False):
    if not client_id:
        return False

    if client_id not in semaphores:
        semaphores[client_id] = threading.Semaphore()

    timeout = 10
    if cancel_if_locked:
        timeout = 0

    val = semaphores[client_id].acquire(blocking=True, timeout=timeout)

    return val


def release_semaphore(client_id):
    if not client_id:
        return False

    if client_id in semaphores:
        semaphores[client_id].release()


@app.before_request
def before_request():
    logger.info("New Request")
    """This runs before every API request. The function take cares of creating
    driver object is not already created. Also it checks for few prerequisits
    parameters and set global variables for other functions to use

    Required paramters for an API hit are:
    auth-key: key string to identify valid request
    client_id: to identify for which client the request is to be run
    """

    if not request.url_rule:
        abort(404)

    logger.info("API call " + request.method + " " + request.url)

    auth_key = request.headers.get("auth-key")
    g.client_id = request.headers.get("client_id")
    rule_parent = request.url_rule.rule.split("/")[1]

    if API_KEY and auth_key != API_KEY:
        abort(401, "you must send valid auth-key")
        logger.error("You must send a valid auth key")
        raise Exception()

    if not g.client_id and rule_parent != "admin":
        abort(400, "client ID is mandatory")
        logger.error("you must send a valid auth ey")

    logger.info("About acquiring semaphore for client" + g.client_id)
    acquire_semaphore(g.client_id)

    # Create a driver object if not exist for client requests.

    if rule_parent != "admin":
        if g.client_id not in drivers:
            logger.info("About to initialise new driver ")
            drivers[g.client_id] = init_client(g.client_id)

        g.driver = drivers[g.client_id]
        g.driver_status = WhatsAPIDriverStatus.Unknown

        if g.driver is not None:
            logger.info("About getting driver status")
            # g.driver_status = WhatsAPIDriverStatus.Unknown
            g.driver_status = g.driver.get_status()
            logger.info("Driver Status - " + g.driver_status)

        # If driver status is unkown, means driver has closed somehow, reopen it
        logger.info("Checking if driver is unknown")
        if (
                g.driver_status != WhatsAPIDriverStatus.NotLoggedIn
                and g.driver_status != WhatsAPIDriverStatus.LoggedIn
        ):
            logger.info("Re-initiaising driver")
            drivers[g.client_id] = init_client(g.client_id)
            g.driver_status = g.driver.get_status()

        init_timer(g.client_id)
        logger.info("subscribing to new messages")
        # g.driver.subscribe_new_messages(NewMessageObserver(g.client_id))


@app.after_request
def after_request(r):
    """This runs after every request end. Purpose is to release the lock acquired
    during staring of API request"""
    if "client_id" in g and g.client_id:
        release_semaphore(g.client_id)
    return r


# -------------------------- ERROR HANDLER -----------------------------------


@app.errorhandler(werkzeug.exceptions.InternalServerError)
def on_bad_internal_server_error(e):
    if "client_id" in g and g.client_id:
        release_semaphore(g.client_id)
    if type(e) is WebDriverException and "chrome not reachable" in e.msg:
        drivers[g.client_id] = init_driver(g.client_id)
        return jsonify(
            {
                "success": False,
                "message": "For some reason, browser for client "
                           + g.client_id
                           + " has closed. Please, try get QrCode again",
            }
        )
    else:
        raise e


"""
#####################
##### API ROUTES ####
#####################
"""


# ---------------------------- Client -----------------------------------------


@app.route("/client", methods=["PUT"])
def create_client():
    """Create a new client driver. The driver is automatically created in
    before_request function."""
    result = False
    if g.client_id in drivers:
        result = True
    return jsonify({"Success": result})


@app.route("/client", methods=["DELETE"])
def delete_client():
    """Delete all objects related to client"""
    preserve_cache = request.args.get("preserve_cache", True)
    delete_client(g.client_id, preserve_cache)
    return jsonify({"Success": True})


# ---------------------------- WhatsApp ----------------------------------------


@app.route("/screen", methods=["GET"])
def get_screen():
    """Capture chrome screen image and send it back. If the screen is currently
    at qr scanning phase, return the image of qr only, else return image of full
    screen"""
    img_title = "screen_" + g.client_id + ".png"
    image_path = STATIC_FILES_PATH + img_title
    if g.driver_status != WhatsAPIDriverStatus.LoggedIn:
        try:
            g.driver.get_qr(image_path)
            return send_file(image_path, mimetype="image/png")
        except Exception as err:
            pass
    g.driver.screenshot(image_path)
    return send_file(image_path, mimetype="image/png")


@app.route("/screen/qr", methods=["GET"])
def get_qr():
    """Get qr as a json string"""
    qr = g.driver.get_qr_plain()
    return jsonify({"qr": qr})


@app.route("/screen/qr/request", methods=["POST"])
def begin_login_timer():
    logger.info("QR requested")
    """ Initialise login timer """
    try:
        init_login_timer(g.client_id)
        logger.info("Timer initialised")
        return jsonify({
            "success": True
        })
    except Exception:
        logger.error("Timer initialisation failed")
        return jsonify({
            "success": False
        })


@app.route("/screen/qr/base64", methods=["GET"])
def get_qr_base64():
    logger.info("QR code in base64 requested")
    """ Get qr as base64 string"""
    try:
        qr = g.driver.get_qr_base64()
        logger.info("Successfully returning QR code as base 64 string")
        return jsonify({
            "success": True,
            "isLoggedIn": False,
            "qr": qr
        })
    except NoSuchElementException:
        phone = g.driver.get_id().replace("\"", "").replace("@c.us", "")
        logger.info("User is logged In, Successfully returning phone number")
        return jsonify({
            "success": True,
            "msisdn": phone,
            "isLoggedIn": True,
            "qr": None
        })


@app.route("/messages/unread", methods=["GET"])
@login_required
def get_unread_messages():
    """Get all unread messages"""
    mark_seen = request.args.get("mark_seen", True)
    unread_msg = g.driver.get_unread()

    if mark_seen:
        for msg in unread_msg:
            msg.chat.send_seen()

    return jsonify(unread_msg)


@app.route("/contacts", methods=["GET"])
@login_required
def get_contacts():
    """Get contact list as json"""
    return jsonify(g.driver.get_contacts())


# ------------------------------- Chats ---------------------------------------


@app.route("/chats", methods=["GET"])
@login_required
def get_chats():
    """Return all the chats"""
    result = g.driver.get_all_chats()
    return jsonify(result)


@app.route("/chats/<chat_id>/messages", methods=["GET"])
@login_required
def get_messages(chat_id):
    """Return all of the chat messages"""

    mark_seen = request.args.get("mark_seen", True)

    chat = g.driver.get_chat_from_id(chat_id)
    msgs = list(g.driver.get_all_messages_in_chat(chat))

    for msg in msgs:
        print(msg.id)

    if mark_seen:
        for msg in msgs:
            try:
                msg.chat.send_seen()
            except:
                pass

    return jsonify(msgs)


@app.route("/chats/<chat_id>/messages", methods=["POST"])
@login_required
def send_message(chat_id):
    """Send a message to a chat
    If a media file is found, send_media is called, else a simple text message
    is sent
    """

    data = request.json
    contents = data.get("contents")
    message = data.get("message")
    chat = g.driver.get_chat_from_id(chat_id)

    logger.info("Payload from R2MP -- Content : "+ contents + "  ~ Message : "+ message)
    if message is not None:
        res = chat.send_message(message)
    for content in contents:
        title = content.get('title')
        intent = content.get('payload')
        image_url = content.get('imageUrl')

        if intent is not None:
            payload[title] = intent
        if image_url is None:
            res = chat.send_message(title)
        else:
            res = chat.send_media(image_url, title)
    if res:
        return jsonify(res)
    else:
        return False

    # files = request.files
    #
    # if files:
    #     res = send_media(chat_id, request)
    # else:
    #     message = request.form.get("message")
    #     logger.info("Sending :" +message + "to " + chat_id)
    #     res = g.driver.chat_send_message(chat_id, message)
    #
    #     if request.form.get("payload") is not None:
    #         payload[message] = request.form.get("payload")
    # if res:
    #     return jsonify(res)
    # else:
    #     return False


@app.route("/messages/<msg_id>/download", methods=["GET"])
@login_required
def download_message_media(msg_id):
    """Download a media file"""
    message = g.driver.get_message_by_id(msg_id)

    if not message or not message.mime:
        abort(404)

    profile_path = create_static_profile_path(g.client_id)
    filename = message.save_media(profile_path, True)

    if os.path.exists(filename):
        return send_file(filename, mimetype=message.mime)

    abort(404)


# --------------------------- Admin methods ----------------------------------


@app.route("/admin/clients", methods=["GET"])
def get_active_clients():
    """Get a list of all active clients and their status"""
    global drivers

    if not drivers:
        return jsonify([])

    result = {client: get_client_info(client) for client in drivers}
    return jsonify(result)


@app.route("/admin/clients", methods=["PUT"])
def run_clients():
    """Force create driver for client """
    clients = request.form.get("clients")
    if not clients:
        return jsonify({"Error": "no clients provided"})

    result = {}
    for client_id in clients.split(","):
        if client_id not in drivers:
            init_client(client_id)
            init_timer(client_id)

        result[client_id] = get_client_info(client_id)

    return jsonify(result)


@app.route("/admin/client", methods=["DELETE"])
def kill_clients():
    """Force kill driver and other objects for a perticular clien"""
    clients = request.form.get("clients")
    kill_dead = request.args.get("kill_dead", default=False)
    kill_dead = kill_dead and kill_dead in ["true", "1"]

    if not kill_dead and not clients:
        return jsonify({"Error": "no clients provided"})

    for client in list(drivers.keys()):
        if kill_dead and not drivers[client].is_logged_in() or client in clients:
            drivers.pop(client).quit()
            try:
                timers[client].stop()
                timers[client] = None
                release_semaphore(client)
                semaphores[client] = None
            except:
                pass

    return get_active_clients()


@app.route("/admin/exception", methods=["GET"])
def get_last_exception():
    """Get last exception"""
    return jsonify(sys.exc_info())


@app.route("/")
def hello():
    return "API is running"


if __name__ == "__main__":
    # todo: load presaved active client ids
    app.run(port=8888, host='0.0.0.0')
