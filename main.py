import logging
import random
import re
import time

from telegram import InlineKeyboardMarkup, InlineKeyboardButton, \
    ReplyKeyboardRemove
from telegram.error import TimedOut
from telegram.ext import Updater, MessageHandler, RegexHandler, Filters, \
    ConversationHandler, CommandHandler, CallbackQueryHandler

from config import telegram_key
from parser import Parser
import phrases as p


# Enable logging
logging.basicConfig(format='[%(asctime)s] [%(levelname)s] [bot]\n%(message)s',
                    datefmt='%d-%m %H:%M:%S',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

MONEY_THRESHOLD = 15


def choices_kbrd(amount: int) -> InlineKeyboardMarkup:
    '''Return an inline keyboard with buttons from 1 to `amount`.'''
    all_buttons = [InlineKeyboardButton(text=str(btn), callback_data=str(btn))
                   for btn in range(1, amount + 1)]
    if amount <= 3:
        return InlineKeyboardMarkup([all_buttons])
    else:
        return InlineKeyboardMarkup([all_buttons[:-2], all_buttons[-2:]])


class Money:
    '''Money object. If initialized with a range, generates an amount within
    that range randomly.'''
    __slots__ = ['amount']

    def __init__(self, amount):
        if isinstance(amount, str):
            self.amount = random.randint(1, int(amount.split()[1]))
        else:
            self.amount = amount


class GameState:
    '''Game state description.'''
    def __init__(self, init_obj: dict):
        self.state_index = init_obj['state_idx']
        self.message_blocks = init_obj['message_blocks']
        self.replies = init_obj.get('replies')

        self.allow_input = init_obj.get('allow_input', False)
        self.is_lethal = init_obj.get('is_lethal', False)
        self.is_victory = init_obj.get('is_victory', False)

    def present(self, bot, update, user_data: dict):
        '''Present a state to the user based on their visited states and
        modifiers.'''
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
        if current_message and not current_message.isspace():
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
            user_data['filtered'] = filtered
            reply_choices = '\n'.join(
                '{}) {}'.format(idx, reply['text'])
                for idx, reply in enumerate(filtered, 1)
            )

            if not reply_choices:
                return

            update.message.reply_text(
                reply_choices.format(char_name=user_data['char_name'],
                                     last_earn=user_data['last_earn'],
                                     money=user_data['money']),
                parse_mode='markdown',
                reply_markup=choices_kbrd(len(filtered))
            )

    def filter_replies(self, user_data: dict) -> list:
        '''Remove the replies from the current state that are inappropriate
        for the user based on their visited states and modifiers.'''
        final_replies = []
        for reply in self.replies:
            if 'condition' not in reply:
                final_replies.append(reply)
            elif self.check(user_data, reply['condition']):
                final_replies.append(reply)

        return final_replies

    @staticmethod
    def check(user_data: dict, condition: str) -> bool:
        '''Check if the condition is met for the passed user data.'''
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
    '''Class for managing game states presentation and user interaction with
    the bot.'''
    def __init__(self, game_file: str):
        '''Initialize a game by passing a filename of a MD game description.'''
        self.game_states = {init_obj['state_idx']: GameState(init_obj)
                            for init_obj in Parser().parse(game_file)}

    def __getitem__(self, state: int):
        '''Called when a certain state is requested. Based on the state
        properties, certain types of user input are accepted.'''
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
                    RegexHandler('^[a-zA-Z]{3}$',
                                 self.input_wrong,
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
                                 pass_user_data=True),
                    CallbackQueryHandler(self.process_choice,
                                         pass_user_data=True)]

    def get(self, state: int):
        '''Imitate the `dict` interface.'''
        return self[state]

    def values(self):
        # For some reason, ConversationHandler needs to keep references to
        # all available states by requesting the .values(). Returning the
        # states would require building them all, which seems rather useless,
        # considering that returning [] doesn't seem to affect the bot.
        return []

    def prompt_name(self, bot, update):
        '''Ask the user to name their character.'''
        user_id = update.message.from_user.id
        logger.debug('new user {} started a conversation'.format(user_id))
        update.message.reply_text(p.NAME_PROMPT,
                                  reply_markup=ReplyKeyboardRemove())
        return 0

    def save_name(self, bot, update, user_data: dict):
        '''Write down the name and initialize the `user_data` object
        before the game.'''
        user_data.clear()
        user_data['current_state'] = self.game_states[1]
        user_data['char_name'] = update.message.text
        user_data['visited_states'] = [1]
        user_data['modifiers'] = set()
        user_data['last_earn'] = None
        user_data['money'] = 0
        update.message.reply_text(p.NAME_ACCEPTED)
        user_data['current_state'].present(bot, update, user_data)
        return 1

    def process_choice(self, bot, update, user_data: dict) -> int:
        '''Handle the user's reply choice, sent either with an inline button
        or a text message.'''
        if update.callback_query is not None:
            choice = int(update.callback_query.data)
            update.callback_query.answer()
        else:
            choice = int(update.message.text)

        try:
            new_state = user_data['filtered'][choice - 1]['dest_state']
        except IndexError:
            update.callback_query.message.reply_text(p.INVALID_CHOICE)
            return user_data['current_state'].state_index
        user_data['current_state'] = self.game_states[new_state]
        user_data['visited_states'].append(new_state)
        user_data['current_state'].present(bot,
                                           update.callback_query or update,
                                           user_data)
        return new_state

    def input_correct(self, bot, update, user_data: dict) -> int:
        'Handle the case where the user enters the correct code for the lock.'
        new_state = self.on_correct_input
        user_data['current_state'] = self.game_states[new_state]
        user_data['current_state'].present(bot, update, user_data)
        return new_state

    def input_wrong(self, bot, update, user_data: dict) -> int:
        'Handle the case where the user enters the wrong code for the lock.'
        new_state = self.on_wrong_input
        user_data['current_state'] = self.game_states[new_state]
        user_data['current_state'].present(bot, update, user_data)
        return new_state

    def input_random(self, bot, update, user_data: dict) -> int:
        '''Handle the case where the user enters something
        different from a code.'''
        update.message.reply_text(p.RANDOM_INPUT)
        return user_data['current_state'].state_index


def error_handler(bot, update, error):
    if error == TimedOut:
        return
    logger.error(error)


def main():
    """Start the bot"""
    updater = Updater(telegram_key)
    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    state_manager = GameStateManager('im-dreaming.md')

    dp.add_handler(ConversationHandler(
        entry_points=[CommandHandler('start', state_manager.prompt_name)],
        states=state_manager,
        fallbacks=[],
        allow_reentry=True
    ))

    # Logging errors
    dp.add_error_handler(error_handler)

    # Actually start the bot
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
