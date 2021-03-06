#!/usr/bin/env python2.7

# =============================================================================
# IMPORTS
# =============================================================================

import ConfigParser
import MySQLdb
import praw
from praw.errors import APIException, RateLimitExceeded
import re
from requests.exceptions import HTTPError, ConnectionError, Timeout
from socket import timeout
from time import gmtime, strftime
from enum import Enum
import nltk

# =============================================================================
# GLOBALS
# =============================================================================

config = ConfigParser.ConfigParser()
config.read("asoiafsearchbot.cfg")

# Database info
host = config.get("SQL", "host")
user = config.get("SQL", "user")
passwd = config.get("SQL", "passwd")
db = config.get("SQL", "db")
table = config.get("SQL", "table")
column1 = config.get("SQL", "column1")
column2 = config.get("SQL", "column2")

MAX_ROWS = 30
BOOK_CONTAINER = []
sent_tokenize = nltk.data.load('tokenizers/punkt/english.pickle')

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

class Title(Enum):
    All = 0
    AGOT = 1
    ACOK = 2
    ASOS = 3
    AFFC = 4
    ADWD = 5


class Books(object):
    """
    Book class, holds methods to find the correct occurrence
    of the given search term in each chapter.
    """
    # commented already messaged are appended to avoid messaging again
    commented = []
    
    # already searched terms
    # TODO: Make this functionality
    termHistory = {}
    termHistorySensitive = {}
    
    def __init__(self, comment):
        self.comment = comment
        self.bookCommand = None
        self.title = None
        self._bookContainer = None
        self._searchTerm = ""
        self._bookQuery = ""
        self._sensitive = None
        self._listOccurrence = []
        self._rowOccurrence = 0
        self._total = 0
        self._rowCount = 0
        self._commentUser = ""
        self._message = ""

    def parse_comment(self):
        """
        Changes user comment from:
            Lorem ipsum dolor sit amet, consectetur adipiscing elit.
            ullam laoreet volutpat accumsan.
            SearchAll! "SEARCH TERM"
        into finally just:
            SEARCH TERM
        """

        # Removes everything before and including Search.!
        self._searchTerm = ''.join(re.split(
                                r'Search(All|AGOT|ACOK|ASOS|AFFC|ADWD)!', 
                                self.comment.body)[2:]
                            )

        # Kept for legacy reasons
        search_brackets = re.search('"(.*?)"', self._searchTerm)
        if search_brackets:
            self._searchTerm = search_brackets.group(0)
        search_tri = re.search('\((.*?)\)', self._searchTerm)
        if search_tri:
            self._searchTerm = search_tri.group(0)
        
        # quotations at start and end
        # fringes cases for when people don't do it right
        if search_brackets or search_tri:
            self._searchTerm = self._searchTerm[1:-1]
            self._searchTerm = self._searchTerm.strip()

    def from_database_to_dict(self):
        """
        Transfers everything from the database to a tuple type
        """
        grabDB = Connect()
        query = (
            'SELECT * from {table} WHERE MATCH({col1})' 
            'AGAINST(\'"{term}"\' IN BOOLEAN MODE) {bookQuery}'
            'ORDER BY FIELD' 
            '({col2}, "AGOT", "ACOK", "ASOS", "AFFC", "ADWD")'
            ).format(
                table = table,
                term = self._searchTerm,
                bookQuery = self._bookQuery,
                col1 = column1,
                col2 = column2)
        grabDB.execute(query)

        # Each row counts as a chapter
        self._bookContainer = grabDB.fetchall()
        grabDB.close()

    def find_the_search_term(self,rowsTooLong=None):
        """
        Search through the books which chapter holds the search
        term. Then count each use in said chapter. Recursion used
        for when above 30. Makes it so only top 30 results are shown.
        """
        # Sort from highest occurrence to lowest, only top 30
        if rowsTooLong:
            self._listOccurrence[:] = [] 
            holdList = rowsTooLong
            holdList = sorted(holdList, key=lambda tup: tup[6], reverse=True)
        else:
            # Allows the tuple to be changable
            holdList = list(self._bookContainer)
        for i in range(len(holdList)):
            # checks if the word is in that chapter
            foundTerm = re.findall(r"\b" + self._searchTerm +
                r"\b", holdList[i][5], flags=re.IGNORECASE)
            # count the occurrence
            storyLen = len(foundTerm)
            holdList[i] += (storyLen,)


            if foundTerm:
                # keep the count during the recursion
                if not rowsTooLong:
                    self._total += storyLen
                    self._rowCount += 1
                    # adds to the end of holdList[i], it now exists during the recursion
                    holdList[i] += (self.sentences_to_quote(holdList[i][5]), )
                # Stores each found word as a list of strings
                self._listOccurrence.append(
                    "| {series}| {book}| {number}| {chapter}| {pov}| {occur}| {quote}".format(
                        series = holdList[i][0],
                        book = holdList[i][1],
                        number = holdList[i][2],
                        chapter = holdList[i][3],
                        pov = holdList[i][4],
                        occur = holdList[i][6],
                        quote = holdList[i][7]
                    )
                )

                # Ends the recursion loop
                if i >= MAX_ROWS and rowsTooLong:
                        break

        # recursion to sort with top 30, checks at the end of loop
        if self._rowCount >= MAX_ROWS and not rowsTooLong:
            self.find_the_search_term(rowsTooLong=holdList)

    def sentences_to_quote(self, chapter):
        """
        Seperates the chapter into sentences
        Returns the first occurrence of the word in the sentence
        """

        # Seperate the chapters into sentences
        searchSentences = sent_tokenize.tokenize(chapter, realign_boundaries=True)
        findIt = r"\b" + self._searchTerm + r"\b"
        for word in searchSentences:
            regex = (re.sub(findIt,  
                "**" + self._searchTerm.upper() + "**", 
                word, flags=re.IGNORECASE))
            if regex != word:
                return regex
        
    def which_book(self):
        """
        self.title holds the farthest book in the series the
        SQL statement should go. So if the title is ASOS it will only
        do every occurence up to ASOS ONLY for SearchAll!
        """
        # When command is SearchAll! the specific searches 
        # will instead be used. example SearchASOS!
        if self.bookCommand.name != 'All':
            self._bookQuery = ('AND {col2} = "{book}"'
                ).format(col2 = column2,
                        book = self.bookCommand.name)
        # Starts from AGOT ends at what self.title is
        # Not needed for All(0) because the SQL does it by default
        elif self.title.value != 0:
            # First time requires AND, next are ORs
            self._bookQuery += ('AND ({col2} = "{book}" '
                ).format(col2 = column2,
                        book = 'AGOT')
            # start the loop after AGOT
            for x in range(2, self.title.value+1):
                # assign current loop the name of the enum's value
                curBook = Title(x).name
                # Shouldn't add ORs if it's AGOT
                if Title(x) != 1:
                    self._bookQuery += ('OR {col2} = "{book}" '
                        ).format(col2 = column2,
                                book = curBook)
            self._bookQuery += ")" # close the AND in the MSQL

    def build_message(self):
        """
        Build message that will be sent to the reddit user
        """
        commentUser = (
                "**SEARCH TERM: {term}** \n\n "
                "Total Occurrence: {totalOccur} \n\n"
                "Total Chapters: {totalChapter} \n\n"
                "{warning}"
                ">{message}"
                "\n_____\n" 
                "[^([More Info Here])]"
                "(http://www.reddit.com/r/asoiaf/comments/25amke/"
                "spoilers_all_introducing_asoiafsearchbot_command/) | "
                "[^([Practice Thread])]"
                "(http://www.reddit.com/r/asoiaf/comments/26ez9u/"
                "spoilers_all_asoiafsearchbot_practice_thread/) | "
                "[^([Suggestions])]"
                "(http://www.reddit.com/message/compose/?to=RemindMeBotWrangler&subject=Suggestion) | "
                "[^([Code])]"
                "(https://github.com/SIlver--/asoiafsearchbot-reddit)"

            )
        warning = ""
        if self.title.name != 'All':
            warning = ("**ONLY** for **{book}** and under due to the spoiler tag in the title. " 
                "Try the practice thread to reduce spam and keep the thread on topic.\n\n").format(
                            book = self.title.name,
            )
        if self._rowCount >= MAX_ROWS:
            warning += ("Excess number of chapters. Sorted by highest to lowest, top 30 results only.\n\n")
        # Avoids spam and builds table heading only when condition is met
        if self._total > 0:
            self._message += (
                "| Series| Book| Chapter| Chapter Name| Chapter POV| Occurrence| Quote^(First Occurrence Only)\n"
            )
            self._message += "|:{dash}|:{dash}|:{dash}|:{dash}|:{dash}|:{dash}|:{dash}|\n".format(dash='-' * 11)
            # Each element added as a new row with new line
            for row in self._listOccurrence:
                self._message += row + "\n"
        elif self._total == 0:
                self._message = "**Sorry no results.**\n\n"
                
        self._commentUser = commentUser.format(
            warning = warning,
            term = self._searchTerm,
            totalOccur = self._total,
            message = self._message,
            totalChapter = self._rowCount
        )
        
    def reply(self, spoiler=False):
        """
        Reply to reddit user. If the search would be a spoiler
        Send different message.
        """
        try:
            if spoiler:
                self._commentUser = (
                    ">**Sorry, fulfilling this request would be a spoiler due to the spoiler tag in this thread. "
                    "Mayhaps try the request in another thread, heh.**\n\n"
                    "\n_____\n" 
                    "[^([More Info Here])]"
                    "(http://www.reddit.com/r/asoiaf/comments/25amke/"
                    "spoilers_all_introducing_asoiafsearchbot_command/) | "
                    "[^([Practice Thread])]"
                    "(http://www.reddit.com/r/asoiaf/comments/26ez9u/"
                    "spoilers_all_asoiafsearchbot_practice_thread/) | "
                    "[^([Suggestions])]"
                    "(http://www.reddit.com/message/compose/?to=RemindMeBotWrangler&subject=Suggestion) | "
                    "[^([Code])]"
                    "(https://github.com/SIlver--/asoiafsearchbot-reddit)"

                )
            
            print self._commentUser
            #self.comment.reply(self._commentUser)

        except (HTTPError, ConnectionError, Timeout, timeout) as err:
            print err
        except RateLimitExceeded as err:
            print err
            time.sleep(10)
        except APIException as err: # Catch any less specific API errors
            print err
        else:
            self.commented.append(self.comment.id)

    def watch_for_spoilers(self):
        """
        Decides what the scope of spoilers based of the title.
        This means that searchADWD! Shouldn't be used in (Spoiler AGOT).
        """
        
        # loop formats each name into the regex
        # then checks regex against the title
        # number used for which_book() loop
        for name, member in Title.__members__.items():
            # Remove first letter incase of cases like GOT
            regex = ("(\(|\[).*({name}|{nameRemove}).*(\)|\])"
                ).format(name = name.lower(), nameRemove = name[1:].lower())
            if re.search(regex, self.comment.link_title.lower()):
                self.title = member
        # these books are not in Title Enum but follows the same guidelines
        # TODO: Fix when new books are added to the database
        if re.search ("(\(|\[).*(published|twow|d&amp;e|d &amp; e"
            "|dunk.*egg|p\s?\&amp;\s?q).*(\)|\])", self.comment.link_title.lower()):
            self.title = Title.All
        # Decides which book the user picked based on the command.
        # SearchAGOT! to SearchADWD!
        for name, member in Title.__members__.items():
            search = ("Search{name}!").format(name = name)
            if search in self.comment.body:
                self.bookCommand = member    



# =============================================================================
# MAIN
# =============================================================================


def main():
    """Main runner"""
    try:
        # Reddit Info
        user_agent = (
                "ASOIAFSearchBot -Help you find that comment"
                "- by /u/RemindMeBotWrangler")
        reddit = praw.Reddit(user_agent = user_agent)
        reddit_user = config.get("Reddit", "username")
        reddit_pass = config.get("Reddit", "password")
        reddit.login(reddit_user, reddit_pass)

    except Exception as err:
        print err

    # Makes sure to keep going when an exception happens
    while True:
        print "start"
        try:
            comments = praw.helpers.comment_stream(
                reddit, 'asoiaftest', limit = 100, verbosity = 0)
            commentCount = 0

            for comment in comments:
                commentCount += 1
                # Makes the instance attribute bookTuple
                allBooks = Books(comment)
                if re.search('Search(All|AGOT|ACOK|ASOS|AFFC|ADWD)!', comment.body):
                    allBooks.watch_for_spoilers()
                    # Note: None needs to be explict as this evalutes to
                    # with Spoilers All as it's 0
                    if allBooks.title != None:
                        allBooks.which_book()
                        # Don't respond to the comment a second time
                        if allBooks.comment.id not in allBooks.commented:  
                            # skips when SearchCOMMAND! is higher than (Spoiler Tag)
                            if (allBooks.bookCommand.value <= allBooks.title.value or
                                allBooks.title.value == 0):
                                allBooks.parse_comment()
                                allBooks.from_database_to_dict()
                                allBooks.find_the_search_term()
                                allBooks.build_message()
                                allBooks.reply()
                            elif allBooks.comment.id not in allBooks.commented:
                                allBooks.reply(spoiler=True)
                    elif allBooks.comment.id not in allBooks.commented:
                        # Sends apporiate message if it's a spoiler
                        allBooks.reply(spoiler=True)
                if commentCount == 100:
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

