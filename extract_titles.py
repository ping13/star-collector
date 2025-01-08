import sys
import diskcache
from transformers import pipeline, AutoTokenizer

MODEL= "czearing/article-title-generator"
pipe = pipeline(
    "summarization",
    model=MODEL,
)

# use cache to save time fro subseuent runs
cache = diskcache.Cache("./title-generator.cache")

@cache.memoize()
def extract_title(text):
    max_length = 20

    # Check the length of the text and only proceed only, if the text is
    # sufficently long enough to justify a title creation
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    tokens = tokenizer.tokenize(text,max_length=max_length,truncation=True)  # Get tokenized text
    num_tokens = len(tokens)  # Count tokens
    if num_tokens < max_length:
        return text
    
    # Generate summary    
    result =  pipe(text,min_length = 10, max_length = 20)
    if len(result) == 0:
        return text[80] # this is arbitrarily 
    else:
        return result[0]['summary_text'].replace("\n", " ")


if __name__ == "__main__":
    text = sys.stdin.read().strip()
    print(extract_title(text))
