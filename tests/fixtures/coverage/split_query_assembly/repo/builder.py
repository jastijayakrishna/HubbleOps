HEAD = "SELECT conversion_action.id"
TAIL = "FROM conversion_action"


def build():
    return HEAD + " " + TAIL


def run(service, customer_id):
    return service.search(customer_id=customer_id, query=build())
