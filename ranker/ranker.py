import math
from collections import Counter

from config import LINK_WEIGHT, TFIDF_WEIGHT
from database.db import (
    get_all_pages_with_body,
    get_idf_scores,
    get_inbound_links_map,
    save_idf_scores,
)
from indexer.indexer import process_text


def compute_tf(text):
    """Return {word: frequency} for one document's text (stemmed, stopword-free)."""
    tokens = process_text(text)
    return dict(Counter(tokens))


def compute_idf(all_texts):
    """
    Return {word: idf} across a corpus.
    all_texts is a list of raw document strings (one per page).
    Uses the standard smoothed IDF: log(N / (1 + df)) + 1.
    """
    num_docs = len(all_texts)
    doc_freq = Counter()

    for text in all_texts:
        words = set(process_text(text))
        for word in words:
            doc_freq[word] += 1

    return {
        word: math.log(num_docs / (1 + df)) + 1
        for word, df in doc_freq.items()
    }


def compute_and_cache_idf():
    """Recompute IDF scores from the whole corpus and persist them in the database."""
    pages = get_all_pages_with_body()
    all_texts = [page['body'] or '' for page in pages]
    idf_scores = compute_idf(all_texts)
    save_idf_scores(idf_scores)
    print(f'Cached IDF scores for {len(idf_scores)} words across {len(all_texts)} documents.')
    return idf_scores


def score_document(query_tokens, doc_tf, idf_scores):
    """Sum TF-IDF score for the query tokens that appear in this document."""
    score = 0.0
    for token in query_tokens:
        tf = doc_tf.get(token, 0)
        idf = idf_scores.get(token, 0.0)
        score += tf * idf
    return score


def combined_score(tfidf_score, inbound_links, tfidf_weight=TFIDF_WEIGHT, link_weight=LINK_WEIGHT):
    """
    Blend a relevance score (TF-IDF) with a popularity score (inbound links).

    The link count is log-scaled (log1p) so that one extremely popular page
    doesn't completely drown out relevance — going from 1 to 10 inbound links
    matters a lot more than going from 1000 to 1009.
    """
    link_score = math.log1p(inbound_links or 0)
    return tfidf_weight * tfidf_score + link_weight * link_score


def rank_results(query, results):
    """
    Re-rank raw search results (as returned by search_db, each a dict with a
    'url') by a blend of TF-IDF relevance and inbound-link popularity.
    """
    query_tokens = process_text(query)
    idf_scores = get_idf_scores()
    inbound_links_by_url = get_inbound_links_map()

    pages = get_all_pages_with_body()
    body_by_url = {page['url']: page['body'] or '' for page in pages}

    scored = []
    for result in results:
        body = body_by_url.get(result['url'], '')
        doc_tf = compute_tf(body)
        tfidf_score = score_document(query_tokens, doc_tf, idf_scores)
        inbound_links = inbound_links_by_url.get(result['url'], 0)
        final_score = combined_score(tfidf_score, inbound_links)
        scored.append({
            **result,
            'tfidf_score': tfidf_score,
            'inbound_links': inbound_links,
            'final_score': final_score,
        })

    scored.sort(key=lambda r: r['final_score'], reverse=True)
    return scored


if __name__ == '__main__':
    compute_and_cache_idf()
