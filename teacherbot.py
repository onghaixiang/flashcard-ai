from __future__ import annotations

from typing import AsyncIterable
from fastapi_poe.client import stream_request
from fastapi_poe import PoeBot
from fastapi_poe.types import  (PartialResponse, QueryRequest, SettingsRequest, SettingsResponse, ProtocolMessage)
from collections import deque
from time import sleep
import requests
from PyPDF2 import PdfReader 
POE_API_KEY = ""

def get_marking_prompt(question, attempt, model_answer):
    
    return """
    You are a teaching assistant that helps to mark the answer of a student given small sentences from their textbook. Simply return a score out of 10.\n
    QUESTION: <{}>\n
    MODEL ANSWER: <{}>\n
    ATTEMPT: <{}>\n
    DO NOT type in anything else, other than a number from 1 to 10.\n
    If the user says that they do not know the answer, or that it is blank, you MUST give a 1.""".format(question, model_answer, attempt)

def get_Q_prompt(text: str) -> str:
    return """
    You are a helpful teaching assistant that will create 10 questions and answers from a document. Format the answer so that it isn't plagarised from the document.\n
    Do NOT add anything above or below the question-answer pairs.\n
    Do it in the following format:\n
    Question: <QUESTION> Answer: <ANSWER>\n
    Question: <QUESTION> Answer: <ANSWER>\n
    Question: <QUESTION> Answer: <ANSWER>\n\n
    
    This is the document to create questions from. \n
    DOCUMENT: <{}>\n
    Do NOT add any text above or below the question-answer pairs.\n
    Do NOT acknowledge this prompt. \n
    """.format(text)
    
def get_relevant_subchat(query: QueryRequest) -> str: 
    text = query.query[-1].content.upper()
    if "DOCUMENT:" in text: 
        return "LD" #loading document 
    elif "ANSWER:" in text:
        return "ANS" #answering question
    elif "FLASHCARD" in text:
        return "FLASHCARD"
    else: 
        return "ERROR"

def get_document_text(query: QueryRequest) -> str:
    text = query.query[-1].content
    return text.replace("DOCUMENT:", "")

def get_answer_text(query: QueryRequest) -> str:
    text = query.query[-1].content
    return text.replace("ANSWER:", "")

class TeacherBot(PoeBot):
    def __init__(self):
        self.queue = deque()
        self.start = False
        self.wait = False
        self.currQ = ""
        self.currA = ""
    async def get_response(
        self, query: QueryRequest
    ) -> AsyncIterable[PartialResponse]:
        
        if self.start == False: 
            #read document
            document_text = ""
            if query.query[-1].attachments: 
                link = query.query[-1].attachments[0].url
                response = requests.get(link, stream=True)
                with open("data.pdf", "wb") as file: 
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                reader = PdfReader('data.pdf')
                for page in reader.pages:
                    document_text += page.extract_text()
            else:
                document_text = get_document_text(query)
            yield self.text_event("\nGenerating questions...")
            
            q_prompt = get_Q_prompt(document_text)
            
            message = ProtocolMessage(role="user", content=q_prompt)
            q_query = QueryRequest(query=[message], api_key=POE_API_KEY, type="query", user_id="", conversation_id="", message_id="", version="2.0")
            output = ""
            async for msg in stream_request(request=q_query, bot_name="Claude-instant-100k", api_key=POE_API_KEY):
                output += msg.text
            self.process_qa(output)
            yield self.text_event("\nFlashcards generated!")
            yield self.text_event("\nLet's start!")
            sleep(1)
            print(self.queue)
            self.start = True
            
            q,a =  self.queue.popleft()
            yield self.text_event("\nAnswer the question.")
            sleep(0.8)
            yield self.text_event("\n"+q)
            self.wait = True
            self.currQ = q
            self.currA = a
            return
        
        if query.query[-1].content.upper() == "NO":
            yield self.text_event("Goodbye!")
            return
        
        if self.queue and self.start == True and self.wait == False: 
            q,a =  self.queue.popleft()
            yield self.text_event("\nAnswer the question.")
            sleep(0.8)
            yield self.text_event("\n"+q)
            self.wait = True
            self.currQ = q
            self.currA = a
            return 
        
        if self.wait == True:
            attempt = get_answer_text(query)
            prompt = get_marking_prompt(self.currQ, attempt, self.currA)
            q = self.currQ
            a = self.currA
            self.currQ = ""
            self.currA = ""
            self.wait = False
            message = ProtocolMessage(role="user", content=prompt)
            q_query = QueryRequest(query=[message], api_key=POE_API_KEY, type="query", user_id="", conversation_id="", message_id="", version="2.0")
            output = ""
            async for msg in stream_request(request=q_query, bot_name="GPT-3.5-Turbo", api_key=POE_API_KEY):
                output += msg.text
            yield self.text_event("\nScore: {}".format(output))
            yield self.text_event("\nCorrect answer: {}".format(a))
            score = int(output)
            if not self.queue: 
                if score >= 9: 
                    yield self.text_event("\nAll flashcards answered correctly!")
                    return
                else: 
                    yield self.text_event("\nPlaced back into flashcard queue")
                    yield self.text_event("\n Continue?")
                    self.queue.appendleft((q, a))
                    return
            else: 
                if score >= 9: 
                    yield self.text_event("\nThis flashcard was answered correctly! Removing...")
                    yield self.text_event("\n Continue?")
                    return
                
                elif score >= 5: 
                    self.queue.append((q, a))
                    yield self.text_event("\nPlaced back into flashcard queue")
                    yield self.text_event("\n Continue?")
                    return
                elif score >= 2: 
                    midpoint = len(self.queue)//2
                    self.queue.insert(midpoint, (q,a))
                    yield self.text_event("\nPlaced back into flashcard queue")
                    yield self.text_event("\n Continue?")
                    return
                else: 
                    self.queue.appendleft((q,a))
                    yield self.text_event("\nBe careful! This is a tricky question.")
                    yield self.text_event("\nPlaced back into flashcard queue")
                    yield self.text_event("\n Continue?")
                    
                    return

        yield self.text_event("\nAll flashcards answered correctly!")
        return
        
        
        
    async def get_settings(self, settings: SettingsRequest) -> SettingsResponse:
        return SettingsResponse(
            introduction_message=(
                """Hi, I am your LLM Study Companion. To begin, send me a document you would like to study via text or upload a PDF.
                I will create questions for you to answer and I will grade them accordingly.
                I will space out these questions for you based on your score to help you remember core concepts."""
            ),
            server_bot_dependencies={"GPT-3.5-Turbo": 10}, 
            allow_attachments=True
        )
    def process_qa(self, qa: str): 
        lines = qa.strip().split('\n')
        for line in lines: 
            if "Answer:" in line:
                q,a = line.split("Answer:")
                question = q.replace("Question:", "")
                answer = a.strip()
                self.queue.append((question, answer))
        return