from aiogram.fsm.state import State, StatesGroup


class AddClient(StatesGroup):
    name = State(); threads = State(); telegram = State(); confirm = State()

class LinkSheet(StatesGroup):
    url = State()

class LinkPlan(StatesGroup):
    url = State()

class ManagerMessage(StatesGroup):
    text = State()

class PartialPublication(StatesGroup):
    count = State()

class ResultsFlow(StatesGroup):
    responses = State(); leads = State(); comment = State()

class WeeklyStatsFlow(StatesGroup):
    views = State(); likes = State(); replies = State(); reposts = State(); quotes = State(); new_followers = State(); telegram_clicks = State(); best_post = State(); manager_comment = State()
