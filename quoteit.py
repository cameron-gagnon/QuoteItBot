#! /usr/bin/env python3.4

import praw
import sqlite3
import re
import time
import logging
import logging.handlers
import requests
import json

from configparser import ConfigParser
from sys import exit, stdout, stderr
from requests import exceptions


############################################################################
class Comments:
   

    def __init__(self, r):
        # subreddit to parse through
        # set to /r/all, but could be
        # set to a specific sub if needed
        # r is the praw Reddit Object
        self.r = r
        self.db = Database()
        self.regex = re.compile('QuoteIt! ("[\D\d]*")[\s\-/]*u/([\w-]*)',
                            flags = re.IGNORECASE | re.UNICODE)

    def get_comments_to_parse(self):
        #uses pushift.io to perform a search of "QuoteIt!"
        with requests.Session() as s:
            request = s.get('https://api.pushshift.io/reddit/search?q'\
                            '=%22QuoteIt!%22&limit=100')

            json = request.json()
            self.comments = json['data']

    def search_comments(self):
        log.debug("Searching comments")
        
        results = []
        # goes through each comment and 
        # searches for the keyword string
        for comment in self.comments:
            # convert regular json comment to praw Comment object
            comment = praw.objects.Comment(self.r, comment)
            # parse for them keywords yo!
            quote, user = self.parse_for_keywords(comment.body)
            
            ID = comment.id
            # quote will be true when we match a properly formatted keyword call
            # the db lookup is to make sure we don't reply to the same post
            if quote and not self.db.lookup_ID(ID):
                results.append((comment, quote, user)) 
        
        return results

    def parse_for_keywords(self, comment):
        # search for keyword string
        match = re.findall(self.regex, str(comment))
        try:
            # match will be None if we don't 
            # find the keyword string
            quote = match[0][0]
            user = "/u/" + match[0][1]

        except IndexError:
            quote = False 
            user = False

        return quote, user 



class Respond:

    STATIC_REPLY_TEXT = "Quoting {user}: {quote}\n\n"\
                        "Quote suggested by {poster}"\
                        "\n\n___\n\n"\
                        "^If ^this ^post ^receives ^enough ^upvotes, ^it ^will ^be "\
                        "^submitted ^to ^/r/Quotes!"
    UPVOTE_THRESHOLD = 1
    
    def __init__(self, r):
        self.r = r
        self.regex = re.compile('Quoting (/u/[\w_-]*): ("[\D\d]*")',
                                flags = re.IGNORECASE | re.UNICODE)
        self.db = Database()

    def reply(self, results):
        for comment, quote, user in results:
            try:
                self.reply_quote(comment, quote, user)
                pass 
            except praw.errors.InvalidComment:
                log.warning("Comment was deleted")
                pass
            
            self.db.insert(comment.id)

    def reply_quote(self, comment, quote, user):
        comment_author = str(comment.author)
        reply_string = self.STATIC_REPLY_TEXT.format(user = user,
                                                     quote = quote,
                                                     poster = "/u/"+comment_author)
        
        log.debug("Replying to " + comment_author +
                  " with quote " + quote + " from user " + user)
        
        try:
            comment.reply(reply_string)
            # alert user begin queried of query
            log.debug("Reply sucessful!")

        except praw.errors.RateLimitExceeded as error:
            log.debug("Rate limit exceeded, must sleep for "
                      "{} mins".format(float(error.sleep_time / 60)))
            time.sleep(error.sleep_time)
            # try to reply to the comment again
            comment.reply(reply_string)
            log.debug("Reply sucessful!")

        except praw.errors.HTTPException as error:
            log.debug("HTTPError when replying. Sleeping for 10 seconds")
            log.debug(error)
            time.sleep(10)

    def check_votes(self):
        log.debug("Checking votes")
        # get our quoteitbot
        r = self.r.get_redditor("QuoteItBot")
        # return all comments to see their scores
        # if any are > 10, we will post it to quotes
        # a time interval for last comment can be set
        comments = r.get_comments()
        
        for comment in comments:
            # comment ID here is the comment ID of QuoteItBot's comment with the
            # quote in it
            if (comment.score > self.UPVOTE_THRESHOLD) and not\
                self.db.lookup_post(comment.id):
                self.post_to_quotes(comment)

    def post_to_quotes(self, comment):
        text = comment.body
        # pull out the username and quote from our old post
        match = re.findall(self.regex, text)

        try: 
            log.debug("Match is: ")
            log.debug(str(match))
            username = match[0][0]
            quote = match [0][1]
        
        except IndexError:
            log.debug("Not an actual quote comment, index error returned")
            self.db.insert_post(comment.id) 
            return 

        title = "[QuoteItBot] " + quote + " - " + username
        # gets lots of submission data and pieces it together so we can have the premalink to the top level comment
                        # base url                 what subreddit we in?
        formatted_url = "https://reddit.com/r/" + str(comment.subreddit) + "/comments/" +\
                        comment.link_id[3:] + "/" + comment.link_title + "/" + comment.parent_id[3:]           
                        # id of the link in sub     title of submission        id of the parent comment of the 
                                                                               #quoteitbot one

        body = "[Original quote source](" + formatted_url + ")."

        try:
            log.debug("Submitting quote: " + title)
            self.r.submit("Quotes", title, text = body)
            
            log.debug("Submission sucessful!")
            self.db.insert_post(comment.id)

        except praw.errors.RateLimitExceeded as error:
            log.debug("Rate limit exceeded for posting, must sleep for "
                      "{} mins".format(float(error.sleep_time / 60)))
            time.sleep(error.sleep_time)
            # try to reply to the comment again
            self.r.submit("Quotes", title, body)
            log.debug("Submission sucessful!")


###########################################################################
class Database:

    def __init__(self):
        # connect to and create DB if not created yet
        self.sql = sqlite3.connect('IDs.db')
        self.cur = self.sql.cursor()

        self.cur.execute('CREATE TABLE IF NOT EXISTS\
                          quotes(ID TEXT, ID_post TEXT)')
        self.sql.commit()

    def insert(self, ID):
        """
        Add ID to comment database so we know we already replied to it
        """
        self.cur.execute('INSERT INTO quotes (ID) VALUES (?)', [ID])
        self.sql.commit()

        log.debug("Inserted " + str(ID) + " of comment into database!")

    def insert_post(self, ID):
        self.cur.execute('INSERT INTO quotes (ID_post) VALUES (?)', [ID])
        self.sql.commit()

        log.debug("Inserted " + str(ID) + " of post into database.")

    def lookup_ID(self, ID):
        """
        See if the ID has already been added to the database.
        """
        self.cur.execute('SELECT * FROM quotes WHERE ID=?', [ID])
        result = self.cur.fetchone()
        return result

    def lookup_post(self, ID):
        self.cur.execute('SELECT * FROM quotes WHERE Id_post=?', [ID])
        result = self.cur.fetchone()
        return result


###########################################################################
    def format_string(self):
        reply_footer = "\n___\n"\
                       "^| [^About ^me](https://www.reddit.com/r/BotGoneWild/comments/"\
                       "3ifrj5/information_about_botgonewild_here/?ref=share&ref_source=link) "\
                       "^| [^Code](https://github.com/cameron-gagnon/botgonewild) "\
                       "^| [^Click ^to ^be ^removed ^from ^queries](https://www.reddit.com/"\
                       "message/compose/?to=BotGoneWild&subject=Blacklist&message=Please%20"\
                       "remove%20me%20from%20your%20queries.) "\
                       '^| ^Syntax: ^"Has ^/u/username ^gone ^wild?" '\

##############################################################################
# Makes stdout and stderr print to the logging module
def config_logging():
    """ Configures the logging to external file """
    global log
    
    # set file logger
    rootLog = logging.getLogger('')
    rootLog.setLevel(logging.DEBUG)
    
    # make it so requests doesn't show up all the time in our output
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    # apparently on AWS-EC2 requests is used instead of urllib3
    # so we have to silence this again... oh well.
    logging.getLogger('requests').setLevel(logging.CRITICAL)

    # set format for output to file
    formatFile = logging.Formatter(fmt='%(asctime)-s %(levelname)-6s: '\
                                       '%(lineno)d : %(message)s',
                                   datefmt='%m-%d %H:%M')
    
    # add filehandler so once the filesize reaches 5MB a new file is 
    # created, up to 3 files
    fileHandle = logging.handlers.RotatingFileHandler("crash.log",
                                                      maxBytes=5000000,
                                                      backupCount=5,
                                                      encoding = "utf-8")
    fileHandle.setFormatter(formatFile)
    rootLog.addHandler(fileHandle)
    
    # configures logging to console
    # set console logger
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG) #toggle console level output with this line
    
    # set format for console logger
    consoleFormat = logging.Formatter('%(levelname)-6s %(message)s')
    console.setFormatter(consoleFormat)
    
    # add handler to root logger so console && file are written to
    logging.getLogger('').addHandler(console)
    log = logging.getLogger('quoteit')
    stdout = LoggerWriter(log.debug)
    stderr = LoggerWriter(log.warning)

###############################################################################
class LoggerWriter:
    def __init__(self, level):
        self.level = level

    def write(self, message):
        # eliminate extra newlines in default sys.stdout
        if message != '\n':
            self.level(message)

    def flush(self):
        self.level(sys.stderr)


###############################################################################
def connect():
    log.debug("Logging in...")
    
    r = praw.Reddit("browser-based:QuoteIt script for /r/quotes:v0.4 (by /u/camerongagnon)")
    
    config = ConfigParser()
    config.read("login.txt")
    
    username = config.get("Reddit", "username")
    password = config.get("Reddit", "password")
    
    r.login(username, password, disable_warning=True)
    
    return r


###############################################################################
def main():
    try:
        r = connect()
        db = Database()
        while True:    
            try:
                com = Comments(r)
                com.get_comments_to_parse()
                results = com.search_comments()
                
                posts = Respond(r)
                posts.reply(results)
                posts.check_votes()
                
                log.debug("Sleeping...")
                time.sleep(10)
        
            except (exceptions.HTTPError, exceptions.Timeout, exceptions.ConnectionError) as err:
                log.warning("HTTPError, sleeping for 10 seconds")
                log.warning(err)
                time.sleep(10)
                continue

            except Exception as err:
                log.warning(err)
                time.sleep(30)
                continue

    except KeyboardInterrupt:
        log.debug("Exiting")
        exit(0)


###############################################################################
#### MAIN ####
###############################################################################
if __name__ == '__main__':
    config_logging()
    main()
