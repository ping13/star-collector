import sys
from transformers import pipeline, AutoTokenizer
from pprint import pprint
import diskcache

MODEL= "czearing/article-title-generator"
pipe = pipeline(
    "summarization",
    model=MODEL,
)

cache = diskcache.Cache("./title-generator.cache")

@cache.memoize()
def extract_title(text):
    max_length = 20

    # Check the length of the text and only prroceed if the text is long
    # enough.
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    tokens = tokenizer.tokenize(text,max_length=max_length,truncation=True)  # Get tokenized text
    num_tokens = len(tokens)  # Count tokens
    if num_tokens < max_length:
        return text
    
    # Generate summary    
    result =  pipe(text,min_length = 10, max_length = 20)
    return result[0]['summary_text'].replace("\n", " ")


if __name__ == "__main__":
    text = sys.stdin.read().strip()
    pprint(extract_title(text))
