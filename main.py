import logging
import random
import re
import time

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import TimedOut
from telegram.ext import Updater, MessageHandler, RegexHandler, Filters, \
    ConversationHandler, CommandHandler

from config import telegram_key
from parser import Parser
import phrases as p


# Enable logging
logging.basicConfig(format='[%(asctime)s] [%(levelname)s] [bot]\n%(message)s',
                    datefmt='%d-%m %H:%M:%S',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

MONEY_THRESHOLD = 15


def choices_kbrd(amount):
    all_buttons = list(map(str, range(1, amount + 1)))
    if amount <= 3:
        return ReplyKeyboardMarkup([all_buttons],
                                   one_time_keyboard=True,
                                   resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup([all_buttons[:-2], all_buttons[-2:]],
                                   one_time_keyboard=True,
                                   resize_keyboard=True)


class Money:
    __slots__ = ['amount']

    def __init__(self, amount):
        if isinstance(amount, str):
            self.amount = random.randint(1, int(amount.split()[1]))
        else:
            self.amount = amount


class GameState:
    def __init__(self, init_obj):
        self.state_index = init_obj['state_idx']
        self.message_blocks = init_obj['message_blocks']
        self.replies = init_obj.get('replies')

        self.allow_input = init_obj.get('allow_input', False)
        self.is_lethal = init_obj.get('is_lethal', False)
        self.is_victory = init_obj.get('is_victory', False)

    def present(self, update, user_data):
        current_message = ''
        for block in self.message_blocks:
            if not self.check(user_data, block.get('condition')):
                continue

            for component in block['content']:
                if component['type'] == 'message':
                    current_message += component['content']
                elif component['type'] == 'mbreak':
                    update.message.reply_text(
                        current_message.format(
                            char_name=user_data['char_name'],
                            last_earn=user_data['last_earn'],
                            money=user_data['money']
                        ),
                        parse_mode='markdown'
                    )
                    current_message = ''
                    time.sleep(component.get('content'))
                elif component['type'] == 'modifier':
                    content = component['content']
                    if content.startswith('not'):
                        user_data['modifiers'].remove(content.split()[1])
                    else:
                        user_data['modifiers'].add(content)
                elif component['type'] == 'money':
                    money_obj = Money(component['content'])
                    user_data['last_earn'] = money_obj.amount
                    user_data['money'] += money_obj.amount
                    if user_data['money'] >= MONEY_THRESHOLD:
                        user_data['modifiers'].add('money')
        if current_message:
            update.message.reply_text(
                current_message.format(
                    char_name=user_data['char_name'],
                    last_earn=user_data['last_earn'],
                    money=user_data['money']
                ),
                parse_mode='markdown'
            )

        if self.is_lethal:
            update.message.reply_text(p.WASTED)
            user_data.clear()
        elif self.is_victory:
            update.message.reply_text(p.VICTORY)
            user_data.clear()
        else:
            filtered = self.filter_replies(user_data)
            reply_choices = '\n'.join(
                '{}) {}'.format(idx, reply['text'])
                for idx, reply in enumerate(filtered, 1)
            )
            update.message.reply_text(reply_choices,
                                      parse_mode='markdown',
                                      reply_markup=choices_kbrd(len(filtered)))

    def filter_replies(self, user_data):
        final_replies = []
        for reply in self.replies:
            if 'condition' not in reply:
                final_replies.append(reply)
            elif self.check(user_data, reply['condition']):
                final_replies.append(reply)

        return final_replies

    @staticmethod
    def check(user_data, condition):
        if condition is None:
            return True

        if 'and' in condition:
            for part in condition.split(' and '):
                if part.startswith('not'):
                    act_part = part.split()[1]
                    absence = True
                else:
                    act_part = part
                    absence = False

                field = 'visited_states' if act_part.isdigit() else 'modifiers'
                if act_part.isdigit():
                    act_part = int(act_part)
                if not (absence ^ (act_part in user_data[field])):
                    return False
            return True

        if condition.startswith('not'):
            act_condition = condition.split()[1]
            absence = True
        else:
            act_condition = condition
            absence = False
        field = 'visited_states' if act_condition.isdigit() else 'modifiers'
        if act_condition.isdigit():
            act_condition = int(act_condition)
        return absence ^ (act_condition in user_data[field])


class GameStateManager:
    def __init__(self, game_file):
        self.game_states = {init_obj['state_idx']: GameState(init_obj)
                            for init_obj in Parser().parse(game_file)}
        self.current_state = self.game_states[1]

    def __getitem__(self, state):
        if state == 0:
            return [MessageHandler(Filters.text,
                                   self.save_name,
                                   pass_user_data=True)]

        state_obj = self.game_states[state]

        if state_obj.allow_input:
            self.on_correct_input, self.on_wrong_input = state_obj.allow_input
            return [RegexHandler(re.compile('ASK', re.I),
                                 self.input_correct,
                                 pass_user_data=True),
                    RegexHandler('[a-zA-Z]{3}',
                                 self.input_incorrect,
                                 pass_user_data=True),
                    RegexHandler('1',
                                 self.process_choice,
                                 pass_user_data=True),
                    MessageHandler(Filters.text,
                                   self.input_random,
                                   pass_user_data=True)]
        else:
            return [RegexHandler('[0-9]+',
                                 self.process_choice,
                                 pass_user_data=True)]

    def get(self, state):
        return self[state]

    def values(self):
        return []

    def prompt_name(self, bot, update):
        user_id = update.message.from_user.id
        logger.debug('new user {} started a conversation'.format(user_id))
        update.message.reply_text(p.NAME_PROMPT,
                                  reply_markup=ReplyKeyboardRemove())
        return 0

    def save_name(self, bot, update, user_data):
        user_data.clear()
        self.current_state = self.game_states[1]
        user_data['char_name'] = update.message.text
        user_data['visited_states'] = [1]
        user_data['modifiers'] = set()
        user_data['last_earn'] = None
        user_data['money'] = 0
        update.message.reply_text(p.NAME_ACCEPTED)
        self.current_state.present(update, user_data)
        return 1

    def process_choice(self, bot, update, user_data):
        choice = int(update.message.text)
        try:
            new_state = self.current_state.replies[choice - 1]['dest_state']
        except IndexError:
            update.message.reply_text(p.INVALID_CHOICE)
            return self.current_state.state_index
        self.current_state = self.game_states[new_state]
        user_data['visited_states'].append(new_state)
        self.current_state.present(update, user_data)
        return new_state

    def input_correct(self, bot, update, user_data):
        new_state = self.on_correct_input
        self.current_state = self.game_states[new_state]
        self.current_state.present(update, user_data)
        return new_state

    def input_wrong(self, bot, update, user_data):
        new_state = self.on_wrong_input
        self.current_state = self.game_states[new_state]
        self.current_state.present(update, user_data)
        return new_state

    def input_random(self, bot, update, user_data):
        update.message.reply_text(p.RANDOM_INPUT)
        return self.current_state.state_index

    def reset_game(self, bot, update, user_data):
        update.message.reply_text(p.RESETTING)
        user_data.clear()


def error(bot, update, error):
    if error == TimedOut:
        return
    logger.error(error)


def main():
    """Start the bot"""
    updater = Updater(telegram_key)
    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    state_manager = GameStateManager('text-based-madness.md')

    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler('start', state_manager.prompt_name)],
        states=state_manager,
        fallbacks=[],
        allow_reentry=True
    ))

    # Logging errors
    dp.add_error_handler(error)

    # Actually start the bot
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
