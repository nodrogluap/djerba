"""Simple utility functions for time/date"""

import time

DATE_FORMAT = "%Y-%m-%d"

def get_todays_date():
    return time.strftime(DATE_FORMAT)

def is_valid_date(date_string):
    valid = True
    try:
        time.strptime(date_string, DATE_FORMAT)
    except ValueError:
        valid = False
    return valid
