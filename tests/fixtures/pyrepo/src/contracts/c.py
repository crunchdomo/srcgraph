from src.gateway import client

class Contract:
    def send(self):
        client.post()

def call(req):
    return Contract().send()

def warm():
    pass
