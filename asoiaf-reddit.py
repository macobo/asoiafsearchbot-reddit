#!/usr/bin/env python -O

# =============================================================================
# IMPORTS
# =============================================================================

import ConfigParser
import MySQLdb
import praw
from praw.errors import  APIException, RateLimitExceeded
import re
from requests.exceptions import HTTPError, ConnectionError, Timeout
from socket import timeout
import time

# =============================================================================
# GLOBALS
# =============================================================================

# Reads the config file
config = ConfigParser.ConfigParser()
config.read("asoiafsearchbot.cfg")

user_agent = ("ASOIAFSearchBot -Help you find that comment- by /u/RemindMeBotWrangler")
reddit = praw.Reddit(user_agent=user_agent)

# Reddit Info
reddit_user = config.get("Reddit", "username")
reddit_pass = config.get("Reddit", "password")
reddit.login(reddit_user, reddit_pass)

# Database info
host = config.get("SQL", "host")
user = config.get("SQL", "user")
passwd = config.get("SQL", "passwd")
db = config.get("SQL", "db")
table = config.get("SQL", "table")
column1 = config.get("SQL", "column1")
column2 = config.get("SQL", "column2")

# commented already messaged are appended to avoid messaging again
commented = []

# =============================================================================
# CLASSES
# =============================================================================


class Connect(object):
    """
    DB connection class
    """
    connection = None
    cursor = None

    def __init__(self):
        self.connection = MySQLdb.connect(
            host=host, user=user, passwd=passwd, db=db
        )
        self.cursor = self.connection.cursor()

    def execute(self, command):
        self.cursor.execute(command)

    def fetchall(self):
        return self.cursor.fetchall()

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

# =============================================================================
# PUBLIC FUNCTIONS
# =============================================================================


def parse_comment(comment):
    """
    Parses comment for what term to search for
    Also decides if it's sensitive or insensitive
    """

    search_term = ""
    sensitive = False
    # remove everything before SearchAll!
    # Allows quotations to be used before SearchAll!
    original_comment = comment.body
    original_comment = ''.join(original_comment.split('SearchAll!')[1:])

    if comment not in commented:
        commented.append(comment)
        print "in here"
        # INSENSITIVE
        search_brackets = re.search('"(.*?)"', original_comment)
        if search_brackets:
            search_term = search_brackets.group(0)
            sensitive = False

        # SENSITIVE
        search_tri = re.search('\((.*?)\)', original_comment)
        if search_tri:
            search_term = search_tri.group(0)
            sensitive = True

        # Stop pesky searches like "a"
        if len(search_term) > 3:
            search_db(comment, search_term, sensitive)


def search_db(comment, term, sensitive):
    """
    Queries through DB counts occurrences for each chapter
    """
    search_database = Connect()

    # Take away whitespace and quotations at start and end
    term = term[1:]
    term = term[:len(term) - 1]
    term = term.strip()

    total = 0  # Total Occurrence
    row_count = 0  # How many rows have been searched through

    if not sensitive:
        # INSENSITIVE SEARCH
        search_database.execute(
            'SELECT * FROM {table} WHERE lower({col1}) REGEXP '
            '"([[:blank:][:punct:]]|^){term}([[:punct:][:blank:]]|$)" '
            'ORDER BY FIELD'
            '({col2}, "AGOT", "ACOK", "ASOS", "AFFC", "ADWD")'.format(
                table=table,
                col1=column1,
                term=term,
                col2=column2
            )
        )
        data = search_database.fetchall()
        list_occurrence = []

        # Counts occurrences in each row and
        # adds itself to listOccurrence for message
        for row in data:
            list_occurrence.append(
                "| {series}| {book}| {chapter}| {pov}| {occur}".format(
                    series=row[0],
                    book=row[1],
                    chapter=row[3],
                    pov=row[4],
                    occur=row[5].lower().count(term.lower()),
                )
            )
            total += row[5].lower().count(term.lower())
            row_count += 1

    else:
        # SENSITIVE SEARCH
        search_database.execute(
            'SELECT * FROM {table} WHERE {col1} REGEXP BINARY '
            '"([[:blank:][:punct:]]|^){term}([[:punct:][:blank:]]|$)" '
            'ORDER BY FIELD'
            '({col2}, "AGOT", "ACOK", "ASOS", "AFFC", "ADWD")'.format(
                table=table,
                col1=column1,
                term=term,
                col2=column2
            )
        )
        data = search_database.fetchall()
        list_occurrence = []

        # Counts occurrences in each row and
        # adds itself to listOccurrence for message
        for row in data:
            list_occurrence.append(
                "| {series}| {book}| {chapter}| {pov}| {occur}".format(
                    series=row[0],
                    book=row[1],
                    chapter=row[3],
                    pov=row[4],
                    occur=row[5].count(term),
                )
            )
            total += row[5].lower().count(term.lower())
            row_count += 1

    search_database.close()
    send_message(comment, list_occurrence, row_count, total, term, sensitive)


def send_message(comment, occurrence, row_count, total, term, sensitive):
    """
    Sends message to user with the requested information
    """

    try:
        message = ""
        comment_to_user = (
            "**SEARCH TERM ({0}): {1}** \n\n "
            "Total Occurrence: {2} \n\n"
            "{3} "
            "[Visualization of the search term]"
            "(http://creative-co.de/labs/songicefire/?terms={1})"
            "\n_____\n "
            "^(Hello, I'm ASOIAFSearchBot, I will display the occurrence of "
            "your term and what chapters it was found in.)"
            "[^(More Info Here)]"
            "(http://www.reddit.com/r/asoiaf/comments/25amke/"
            "spoilers_all_introducing_asoiafsearchbot_command/)"
        )

        # Avoid spam, limit amount of rows
        if row_count < 30 and total > 0:
            message += "| Series| Book| Chapter Name| Chapter POV| Occurrence\n"
            message += "|:{dash}|:{dash}|:{dash}|:{dash}|:{dash}|\n".format(
                dash='-' * 11
            )
            # Each element is changed to one string
            for row in occurrence:
                message += row + "\n"
        elif row_count > 30:
            message = "**Excess amount of chapters.**\n\n"
        elif total == 0:
            message = "**Sorry no results.**\n\n"

        if sensitive:
            case_sensitive = "CASE-SENSITIVE"
        else:
            case_sensitive = "CASE-INSENSITIVE"

        comment.reply(
            comment_to_user.format(
                case_sensitive, term, total, message
            )
        )
        print comment_to_user.format(case_sensitive, term, total, message)
    except (HTTPError, ConnectionError, Timeout, timeout) as err:
        print err
    except RateLimitExceeded as err:
        print err
        time.sleep(10)
    except APIException as err:  # Catch any less specific API errors
        print err

# =============================================================================
# MAIN
# =============================================================================


def main():
    """Main runner"""
    while True:
        try:
            print "start    "
            # Grab all new comments from /r/asoiaf
            comments = praw.helpers.comment_stream(
                reddit, 'asoiaf', limit=None, verbosity=0
            )
            comment_count = 0
            # Loop through each comment
            for comment in comments:
                comment_count += 1
                if "SearchAll!" in comment.body:
                    parse_comment(comment)
                # end loop after 1000
                if comment_count == 1000:
                    break
            print "sleeping"
            time.sleep(25)
        except Exception as err:
            print err

# =============================================================================
# RUNNER
# =============================================================================

if __name__ == '__main__':
    main()


























