from pydantic import BaseModel, ConfigDict, Field, model_validator
from ai.schemas import scrub_numbers
import re


class ShopRecommendation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rank:int=Field(ge=1,le=3)
    candidate_id:str=Field(min_length=1,max_length=20)
    reason:str=Field(min_length=1,max_length=200)


class ShopDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    recommendations:list[ShopRecommendation]=Field(min_length=1,max_length=3)
    summary:str=Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def ordered(self):
        if [r.rank for r in self.recommendations]!=list(range(1,len(self.recommendations)+1)):
            raise ValueError('Invalid ranks')
        if len({r.candidate_id for r in self.recommendations})!=len(self.recommendations):
            raise ValueError('Duplicate choices')
        return self


def validate_shop_decision(raw,calculation):
    result=ShopDecision.model_validate_json(raw)
    ids={c['id'] for c in calculation['candidates']}
    for rec in result.recommendations:
        if rec.candidate_id not in ids:raise ValueError('Not a legal shop plan')
        if re.search(r'[0-9%％]',rec.reason):raise ValueError('Numeric claims belong to local calculation only')
        rec.reason=scrub_numbers(rec.reason)
    if re.search(r'[0-9%％]',result.summary):raise ValueError('Numeric claims belong to local calculation only')
    result.summary=scrub_numbers(result.summary)
    return result
