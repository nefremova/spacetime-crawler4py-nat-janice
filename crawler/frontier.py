import os
import shelve

from threading import Thread, RLock
from queue import Queue, Empty

from utils import get_logger, get_urlhash, normalize, split_url
from scraper import is_valid
from urllib.parse import urlparse
import re
from database import Database
import math 

class Frontier(object):
    def __init__(self, config, restart):
        self.logger = get_logger("FRONTIER")
        self.config = config
        self.to_be_downloaded = list()
        self.visited_cache = dict()
        self.fingerprint_cache = dict()
        self.max_webpage_len = 0
        
        if not os.path.exists(self.config.save_file) and not restart:
            # Save file does not exist, but request to load save.
            self.logger.info(
                f"Did not find save file {self.config.save_file}, "
                f"starting from seed.")
        elif os.path.exists(self.config.save_file) and restart:
            # Save file does exists, but request to start from seed.
            self.logger.info(
                f"Found save file {self.config.save_file}, deleting it.")
            os.remove(self.config.save_file)
        # Load existing save file, or create one if it does not exist.
        self.save = shelve.open(self.config.save_file)
        self.print_shelf = shelve.open(self.config.print_shelf)
        self.visit_shelf = shelve.open(self.config.visit_shelf)
        self.len_shelf = shelve.open(self.config.len_shelf)

        if restart:
            db = Database(self.config.db_name)
            db.connect()
            db.clear_db()
            db.close_connection()
            self.clear_shelves()
            for url in self.config.seed_urls:
                self.add_url(url)
        else:
            # Set the frontier state with contents of save file.
            self._parse_save_file()
            if not self.save:
                for url in self.config.seed_urls:
                    self.add_url(url)

    def _parse_save_file(self):
        ''' This function can be overridden for alternate saving techniques. '''
        total_count = len(self.save)
        tbd_count = 0
        for url, completed in self.save.values():
            if not completed and is_valid(url):
                self.to_be_downloaded.append(url)
                tbd_count += 1
        self.logger.info(
            f"Found {tbd_count} urls to be downloaded from {total_count} "
            f"total urls discovered.")

        # load in the caches and max_len state
        for url, fingerprint in self.print_shelf.items():
            self.fingerprint_cache[url] = fingerprint

        for url, val in self.visit_shelf.items():
            self.visited_cache[url] = val

        self.max_webpage_len = self.len_shelf["max_webpage_len"]

    def get_tbd_url(self):
        try:
            return self.to_be_downloaded.pop()
        except IndexError:
            return None

    def add_url(self, url):
        url = normalize(url)
        urlhash = get_urlhash(url)
        if urlhash not in self.save:
            self.save[urlhash] = (url, False)
            self.save.sync()
            self.to_be_downloaded.append(url)
    
    def check_visited_cache(self):
        if len(self.visited_cache) >= self.config.cache_capacity:
            sorted_cache = list(sorted(self.visited_cache.keys(), key=lambda x: self.visited_cache[x]))
            to_delete = sorted_cache[:self.config.cache_dump_amt]

            for i in range(len(to_delete)):
                url = to_delete[i]
                del self.visited_cache[url]
                del self.visit_shelf[url]
                to_delete[i] = split_url(url)

            # move the least visited urls into db 
            try:
                db = Database(self.config.db_name)
                db.connect()
                db.insert_urls(to_delete)
                db.close_connection()
            except Exception as e:
                print("DB ERROR:", e)

            for url in self.visited_cache:
                self.visited_cache[url] = int(self.visited_cache[url] * self.config.rank_dec)
                self.visit_shelf[url] = int(self.visit_shelf[url] * self.config.rank_dec)

            self.visit_shelf.sync()


    def check_fingerprint_cache(self):
        if len(self.fingerprint_cache) >= self.config.cache_capacity:
            sorted_cache = list(sorted(self.fingerprint_cache.keys(), key=lambda x: self.fingerprint_cache[x][1]))
            to_delete = sorted_cache[:self.config.cache_dump_amt]

            # get rid of the least used fingerprints
            for i in range(len(to_delete)):
                url = to_delete[i]
                del self.fingerprint_cache[url]
                del self.print_shelf[url]
            
            for url in self.fingerprint_cache:
                self.fingerprint_cache[url][1] = int(self.fingerprint_cache[url][1] * self.config.rank_dec)
                self.print_shelf[url][1] = int(self.print_shelf[url][1] * self.config.rank_dec)

            self.print_shelf.sync()
        
    def mark_url_complete(self, url):
        urlhash = get_urlhash(url)
        if urlhash not in self.save:
            # This should not happen.
            self.logger.error(
                f"Completed url {url}, but have not seen it before.")

        self.save[urlhash] = (url, True)
        self.save.sync()
    
    def update_max(self, page_len):
        if page_len > self.max_webpage_len:
            self.max_webpage_len = page_len

            self.len_shelf["max_webpage_len"] = page_len
            self.len_shelf.sync()

    def clear_shelves(self):
        for url in self.visit_shelf.keys():
            del self.visit_shelf[url]

        for url in self.print_shelf.keys():
            del self.print_shelf[url]
       
        self.len_shelf["max_webpage_len"] = 0

        self.visit_shelf.sync()
        self.print_shelf.sync()
        self.len_shelf.sync()




