import re


class Parser:
    comp_ptn = re.compile('(<(?:mod|input|mb|money)[^/]*/>)')
    parse_mod = re.compile('<mod ([^/]+)/>')
    parse_input = re.compile('<input \\w+ correct=([0-9]+) wrong=([0-9]+)/>')
    parse_mb = re.compile('<mb(?: wait=([0-9]+))?/>')
    parse_money = re.compile('<money ([^/]+)/>')

    if_ptn = re.compile('(<if [^>]+>.+?</if>)', re.S)
    parse_if = re.compile('<if ([^>]+)>(.+?)</if>', re.S)

    reply_ptn = re.compile('> (?:<if ([^>]+)>)?(.+?) \(([0-9]+)\)(?:</if>)?')

    def __init__(self):
        self.state_input = None

    def break_to_blocks(self, text):
        blocks = []
        for block in self.if_ptn.split(text):
            if block.startswith('<if'):
                condition, content = self.parse_if.fullmatch(block).group(1, 2)
                blocks.append({'condition': condition,
                               'content': self.break_to_components(content)})
            else:
                blocks.append({'content': self.break_to_components(block)})
        return blocks

    def break_to_components(self, text):
        components = []
        for part in self.comp_ptn.split(text):
            if part.startswith('<mod'):
                content = self.parse_mod.fullmatch(part).group(1)
                components.append({'type': 'modifier',
                                   'content': content})
            elif part.startswith('<input'):
                correct, wrong = self.parse_input.fullmatch(part).group(1, 2)
                self.state_input = (int(correct), int(wrong))
            elif part.startswith('<mb'):
                wait = int(self.parse_mb.fullmatch(part).group(1) or 0)
                components.append({'type': 'mbreak',
                                   'content': wait})
            elif part.startswith('<money'):
                content = self.parse_money.fullmatch(part).group(1)
                components.append({'type': 'money',
                                   'content': content})
            else:
                if not part or part.isspace():
                    continue
                part = part.replace('**', '*')
                components.append({'type': 'message',
                                   'content': part.lstrip()})
        return components

    def parse_replies(self, text):
        replies = []
        for condition, text, dest_state in self.reply_ptn.findall(text):
            reply = {}
            if condition:
                reply['condition'] = condition
            reply['text'] = text.replace('**', '*')
            reply['dest_state'] = int(dest_state)
            replies.append(reply)
        return replies

    def parse(self, game_file_name):
        game_md = open(game_file_name).read()
        state_descriptions = game_md.split('\n----\n')
        states = []
        for description in state_descriptions:
            state_obj = {}
            header, *body = description.split('\n\n')
            state_obj['state_idx'] = int(header[4:])
            if body[-1][0] == '>':
                *message, replies = body
                state_obj['message_blocks'] = self.break_to_blocks(
                    '\n\n'.join(message)
                )
                state_obj['replies'] = self.parse_replies(replies)
            elif '**потрачено**' in body[-1]:
                state_obj['is_lethal'] = True
                state_obj['message_blocks'] = self.break_to_blocks(
                    '\n\n'.join(body)
                )
            else:
                state_obj['is_victory'] = True
                state_obj['message_blocks'] = self.break_to_blocks(
                    '\n\n'.join(body)
                )

            if self.state_input is not None:
                state_obj['allow_input'] = self.state_input
                self.state_input = None

            states.append(state_obj)

        return states
