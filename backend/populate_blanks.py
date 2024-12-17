#!/usr/bin/env python
# newer
"""
Finds all entities with blank summaries and tries to create_content for them.
Queries Wikipedia and DDG.
Queries LLM for "summary", "date_started", "date_ended", "corp_fam", "category".

Create / update timeline(s) for entities.
Reads (existing) "summary", "date_started", "date_ended", "corp_fam", "category"
If timeline reads new news items (for entity) since last timeline write.
If no timeline yet reads all news items (for entity).
generate new / updated timeline
fresh Queries to Wikipedia and DDG...
update if needed "summary", "date_started", "date_ended", "corp_fam", "category".
update if needed stage_current and stage_history.
"""

import os
import sys
import logging
script_directory = os.path.dirname(os.path.abspath(__file__))
if script_directory.startswith('/home/bsea/em'):
    mode = 'prod'
    sys.path.append('/var/www/em')
    logpath = '/home/bsea/em/scrape.log'
else:
    mode = 'dev'
    sys.path.append('/home/leet/EnshittificationMetrics/www/')
    logpath = './scrape.log'
logging.basicConfig(level=logging.INFO,
                    filename = logpath,
                    filemode = 'a',
                    format='%(asctime)s -%(levelname)s - %(message)s')
from app import app, db
from app.models import Entity, News
from dotenv import load_dotenv
# pipenv install duckduckgo-search langchain-community
from langchain_community.tools import DuckDuckGoSearchRun
# from langchain_community.tools import DuckDuckGoSearchResults
# from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
# pipenv install wikipedia
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_mistralai.chat_models import ChatMistralAI
import html5lib
import json
import requests
from httpx import HTTPStatusError
import re


llm_api_key = os.getenv('MISTRAL_API_KEY')
llm_temp = 0.25


CREATE_SUMMARY_CONTENT_TEMPLATE = """
Entity is: "{entity}"

For this entity need to write up:
a summary, 
the date entity started, 
date entity ended (none if still current), 
corporate "family" (umbrella) if any, and 
category, if any.

Summary needs to be severely limited in length. 
The maximum summary size which will be stored in database is 1024 characters; 
however for aesthetics and readability in form where summary will be displayed, 
summary size should be 160 characters or less. 
Summary is what the gist of the entity is, what it's about, what it exists to do, its mission. 

Dates are to be in YYYY-MMM-DD format where YYYY is four digit year, MMM is three character month abbreviation, and DD is two (or one) digit day of month. 
Date entity started can be "UNK" if unknown or undetermineable, 
just year if just year and not month and day can not be determined, 
or just year and month if day can not be determined.
For date entity ended return "None" is entity is current and continues to exist.

For corporate family return corporate parent name, "None" if not a subsidiary or child corp, or "UNK" if undetermineable.

For category return "social" if a social media platform, "cloud" if a cloud provider, or "None" if neither of these categories.

Format response _must_ be json, like: 
{{
  "summary": "This is the summary.", 
  "date_started": "2024 JUL 04", 
  "date_ended": "None", 
  "corp_fam": "None", 
  "category": "None"
}}

To obtain date(s), category, family, and summary - relevant Wikipedia page, corporate homepage, and Duck Duck Go search results are provided.

Content pulled from Wikipedia may be incorrect or partially incorrect as containing undisambiguationed hits of the entity name; 
Relevant Wikipedia page content: {wikipedia_page_results} \n

Content from search may be incorrect or partially incorrect due to multiple different hits for a single entity name; 
Duck Duck Go search result content: {ddg_results} \n

For entity "{entity}" return summary, date_started, date_ended, corp_fam, and category.
"""
 

CREATE_TIMELINE_CONTENT_TEMPLATE = """
Entity is "{entity}"; we need to write up its timeline.

A timeline is a short story, up to 4096 characters.  
A history of "{entity}" from it's start to current, or start to finish, (or pre-start to prediction for the future). 
A story arching from stage 1 to 4 of the enshittification spiral. 
Or not, maybe stays stage 1 its whole existence. 
Or maybe stage 1 to swaying / oscillating / bouncing (/ flapping) btwn stages 2 and 3. 
(Actually a weighted avgerages of news judgments, a sequence of stage_current-s captured in stage_history.)
Noteworthy news events along the way (dates should be in YYYY-MMM-DD format) can be critical pieces of the timeline - but not just listing news items verbatim. 
Timeline ur writing is basically a chronology of actions over words resulting in the entities' current brand sentiment. 
Ideally the timeline also gives the gist of what the entity is about, why it exists. 

Only responses formatted in json will be used; like: 
{{
  "timeline": "It was like this; a timeline going from ...", 
}}

"{entity}" started "{date_started}", ended "{date_ended}"; is in corporate family {"corp_fam}", and category "{category}".
"{entity}" currently judged to be at: stage {stage_current} 
"{entity}" summary: {summary} 

News items related to "{entity}" include: 
{news_items}

Wikipedia results (content pulled from Wikipedia may be incorrect or partially incorrect as containing undisambiguationed hits of the entity name): {wikipedia_page_results} 

Duck Duck Go search results (content from search may be incorrect or partially incorrect due to multiple different hits for a single entity name): {ddg_results} 

Please return timeline in json for entity "{entity}".
"""


def create_summary_content(name):
    """
    Query Wikipedia and DDG on name.
    Query LLM w/ wikipedia and DDG results and ask for json of "summary", "date_started", "date_ended", "corp_fam", "category".
    Extract the content between first '{' and last '}'...
    If good json then copy out results, otherwise return summary None.
    """
    summary = None
    date_started = None
    date_ended = None
    corp_fam = None
    category = None
    wikipedia = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
    wikipedia_page_results = wikipedia.run(f'about {name} corp')
    logging.info(f'==> wikipedia_page_results results for {name}: {wikipedia_page_results}.')

    ddg_results = ''
    try:
        search = DuckDuckGoSearchRun()
        ddg_results = search.run(f'about {name} corp')
        logging.info(f'==> ddg_results results for {name}: {ddg_results}.')
    except Exception as e:
        logging.error(f'==> ddg search returned {e}.')
        # raise RatelimitException(f"{resp.url} {resp.status_code} Ratelimit")
        # duckduckgo_search.exceptions.RatelimitException: https://duckduckgo.com/ 202 Ratelimit

    content_prompt = ChatPromptTemplate.from_template(CREATE_SUMMARY_CONTENT_TEMPLATE)
    chain = ( content_prompt
            | large_lang_model 
            | StrOutputParser() 
            )
    try:
        content = chain.invoke({"entity": name, 
                                "wikipedia_page_results": wikipedia_page_results, 
                                "ddg_results": ddg_results})
        logging.info(f'==> Raw content return (which should be json) for {name}:\n{content}')
    except HTTPStatusError as e:
        if e.response.status_code == 401:
            logging.error(f'==> Error: Unauthorized. Please check your API key.')
            # httpx.HTTPStatusError: Error response 401 while fetching https://api.mistral.ai/v1/chat/completions: {"message":"Unauthorized"}
        else:
            logging.error(f'==> chain.invoke Mistral LLM failed: {e}')
    except Exception as e:
        logging.error(f'==> chain.invoke Mistral LLM failed: {e}')
    # extract the content between first '{' and last '}' as LLM tends to be chatty and bookend the needed json with intro and explanation
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1 and start < end:
        content = content[start:end + 1]
        logging.info(f'==> Content btwn first open and last close curly bracket (which should be only json) for {name}:\n{content}')
    try:
        data = json.loads(content)
        summary = data.get('summary')
        date_started = data.get('date_started')
        date_ended = data.get('date_ended')
        corp_fam = data.get('corp_fam')
        category = data.get('category')
    except Exception as e:
        summary = None
        logging.info(f'==> For {name}, unable to process return from LLM into needed variables; got error: {e} ')
    return summary, date_started, date_ended, corp_fam, category


def create_timeline_content(entity):
    """
    Grab all news items linked to entity, for each getting text, summary, date, and stage value.
    Query Wikipedia and DDG on entity timeline.
    Query LLM w/ wikipedia and DDG results, existing summary etc, and ask for json of "timeline".
    Extract the content between first '{' and last '}'...
    If good json then copy out results, otherwise return timeline None.
    """
    timeline = None
    name = entity.name

    news_items = ""
    for item in entity.stage_history: # each list item should be date and stage value, with hopefully a third, news item id
        num_fields = len(item)
        news_items += f"date: {item[0]}; " ### wanna sort by this, do in later pass, add temp var, sort, pass to LLM less confusing
        news_items += f"stage value: {item[1]}; "
        if num_fields == 2:
            news_items += f"no news id; "
        if num_fields == 3:
            target_item_id = item[2]
            news_items += f"news id #{target_item_id}; "
            target = News.query.get(target_item_id) ### Does this need it's own "with app.app_context():" ?
            news_items += f"text: {target.text}; "
            news_items += f"summary: {target.summary}; "
        ### news_items += "\n" # removed this line as LLM spit these back out and unescaped newline characters break json parsing in json.loads later
    logging.info(f'==> news_items for {name}: {news_items}')

    wikipedia = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
    wikipedia_page_results = wikipedia.run(f'timeline about {name} corp')
    logging.info(f'==> wikipedia_page_results results for {name}: {wikipedia_page_results}.')

    ddg_results = ""
    try:
        search = DuckDuckGoSearchRun()
        ddg_results = search.run(f'timeline about {name} corp')
        logging.info(f'==> ddg_results results for {name}: {ddg_results}.')
    except Exception as e:
        logging.error(f'==> ddg search returned {e}.')
        # raise RatelimitException(f"{resp.url} {resp.status_code} Ratelimit")
        # duckduckgo_search.exceptions.RatelimitException: https://duckduckgo.com/ 202 Ratelimit

    content_prompt = ChatPromptTemplate.from_template(CREATE_TIMELINE_CONTENT_TEMPLATE)
    chain = ( content_prompt
            | large_lang_model 
            | StrOutputParser() 
            )
    try:
        content = chain.invoke({"entity": name, 
                                "news_items": news_items, 
                                "summary": entity.summary, 
                                "date_started": entity.date_started, 
                                "date_ended": entity.date_ended, 
                                "corp_fam": entity.corp_fam, 
                                "category": entity.category, 
                                "wikipedia_page_results": wikipedia_page_results, 
                                "ddg_results": ddg_results, 
                                "stage_current": entity.stage_current, })
        logging.info(f'==> Raw content return (which should be json) for "{name}":\n{content}')
    except HTTPStatusError as e:
        content = """No GenAI content available. {"timeline": None}"""
        if e.response.status_code == 401:
            logging.error(f'==> Error: Unauthorized. Please check your API key.')
        else:
            logging.error(f'==> chain.invoke Mistral LLM failed: {e}')
    except Exception as e:
        logging.error(f'==> chain.invoke Mistral LLM failed: {e}')

    pattern = r'^\{\s*\"timeline\":\s*\".*?\"\s*,?\s*\}$'
    if re.search(pattern, content):
        """looks like json"""
        pass
    else:
        start = None
        end = None
        start = content.find('{')
        end = content.rfind('}')
        if start != -1 and end != -1 and start < end:
            """extract the content between first '{' and last '}' as LLM tends to be chatty and bookend the needed json with intro and explanation"""
            content = content[start:end + 1]
    content = content.replace("\n", "\\n") # sanitize, replace literal newline characters
    try:
        logging.info(f'==> Trying json loads for "{name}" with: \n{content}')
        data = json.loads(content)
        timeline = data.get('timeline')
    except Exception as e:
        timeline = None
        logging.info(f'==> For {name}, unable to process return from LLM into needed variables; got error: {e} ')
    return timeline


def large_lang_model(query):
    large_lang_model = ChatMistralAI(model_name = 'open-mixtral-8x7b', 
                                     mistral_api_key = llm_api_key, 
                                     temperature = llm_temp, 
                                     verbose = True )
    return large_lang_model


def parse_for_blank_summary():
    """
    Pulls all entities from DB; skips disabled; if summary None (blank) then call create_content.
    If create_content returns summary None then logs and continues, otherwise sets values for summary etc and commits.
    """
    with app.app_context():
        entities = Entity.query.all()
        for entity in entities:
            if entity.status != 'disabled':
                if entity.summary:
                    continue
                # if entity is not disabled, and has a blank summary, then try to fill in blanks
                summary, date_started, date_ended, corp_fam, category = create_summary_content(name = entity.name)
                if not summary:
                    # if summary comes back None, then go to next entity
                    logging.info(f'==> Tried, but unable to get content for {entity.name}. ')
                    continue
                entity.summary = summary
                entity.date_started = date_started
                entity.date_ended = date_ended
                if not entity.corp_fam:
                    entity.corp_fam = corp_fam
                if not entity.category:
                    entity.category = category
                db.session.commit()
                logging.info(f'==> Populated blanks for {entity.name}:\n summary = {summary}\n date_started = {date_started}\n date_ended = {date_ended}\n corp_fam = {corp_fam}\n category = {category}')
    return None


def parse_for_blank_timeline():
    """
    Pulls all entities from DB; skips disabled; if timeline None (blank) then call create_content.
    If create_content returns timeline None then logs and continues, otherwise sets value for timeline and commits.
    """
    with app.app_context():
        entities = Entity.query.all()
        for entity in entities:
            if entity.status != 'disabled':
                if entity.timeline:
                    continue
                if entity.summary == None:
                    continue
                # if entity is not disabled, and has a blank timeline, but has a summary already, then try to fill in the timeline
                timeline = create_timeline_content(entity)
                if not timeline:
                    # if timeline comes back None, then go to next entity
                    logging.info(f'==> Tried, but unable to get timeline for {entity.name}. ')
                    continue
                entity.timeline = timeline
                db.session.commit()
                logging.info(f'==> Populated timeline for {entity.name}:\n timeline = {timeline}')
    return None


def create_timeline_for_entity(entity_name_str):
    """
    Click in GUI utility submits name of entity selected to run a timeline creation on.
    Pulls entity object from Entities, passes to create_timeline_content.
    Saves new timeline
    """
    with app.app_context():
        entity = Entity.query.filter_by(name=entity_name_str).first()
        if entity.summary == None:
            summary, date_started, date_ended, corp_fam, category = create_summary_content(name = entity_name_str)
            if not summary:
                logging.info(f'==> Tried, but unable to get content for {entity.name}. ')
                logging.info(f'==> Summary needed for timeline, exited before timeline creation. ')
                return None
            entity.summary = summary
            entity.date_started = date_started
            if date_ended:
                entity.date_ended = date_ended
            if corp_fam:
                entity.corp_fam = corp_fam
            if category:
                entity.category = category
            db.session.commit()
            logging.info(f'==> Populated blanks for {entity.name}:\n summary = {summary}\n date_started = {date_started}\n date_ended = {date_ended}\n corp_fam = {corp_fam}\n category = {category}')
        timeline = create_timeline_content(entity)
        if not timeline:
            logging.info(f'==> Tried, but unable to get timeline for {entity.name}. ')
        else:
            entity.timeline = timeline
            db.session.commit()
            logging.info(f'==> Populated timeline for {entity.name}: {timeline}')
    return None



def main():
    logging.info(f'==> +++++++++ starting populate_blanks.py +++++++++++')
    parse_for_blank_summary()
    # parse_for_blank_timeline() ### not ready to go live, test with one at a time
    logging.info(f'==> ++++++++++ ending populate_blanks.py +++++++++++\n')

if __name__ == "__main__":
    main()


