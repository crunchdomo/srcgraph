import logging
from src.contracts import c
from src.db.q import run as db_run

def view(req):
    c.call(req)
    logging.info("x")

def leak():
    db_run()

c.warm()
