import argparse
from typing import *
import shlex
import pytest
import simple_parsing
from simple_parsing import InconsistentArgumentError, ArgumentParser, Formatter
from simple_parsing.wrappers import DataclassWrapper


from simple_parsing.utils import camel_case

xfail = pytest.mark.xfail
parametrize = pytest.mark.parametrize

def xfail_param(*args, reason:str):
    return pytest.param(*args, marks=pytest.mark.xfail(reason=reason))


Dataclass = TypeVar("Dataclass")

class TestSetup():
    @classmethod
    def setup(cls: Type[Dataclass], arguments: Optional[str] = "", dest: Optional[str] = None) -> Dataclass:
        """Basic setup for a test.
        
        Keyword Arguments:
            arguments {Optional[str]} -- The arguments to pass to the parser (default: {""})
            dest {Optional[str]} -- the attribute where the argument should be stored. (default: {None})
        
        Returns:
            {cls}} -- the class's type.
        """
        parser = simple_parsing.ArgumentParser()
        if dest is None:
            dest = camel_case(cls.__name__)
        
        parser.add_arguments(cls, dest=dest)

        if arguments is None:
            args = parser.parse_args()
        else:
            splits = shlex.split(arguments)
            args = parser.parse_args(splits)
        instance: cls = getattr(args, dest) #type: ignore
        return instance
    
    @classmethod
    def setup_multiple(cls: Type[Dataclass], num_to_parse: int, arguments: Optional[str] = "") -> Tuple[Dataclass, ...]:
        parser = simple_parsing.ArgumentParser()
        class_name = camel_case(cls.__name__)
        for i in range(num_to_parse):
            parser.add_arguments(cls, f"{class_name}_{i}")

        if arguments is None:
            args = parser.parse_args()
        else:
            splits = shlex.split(arguments)
            args = parser.parse_args(splits)

        return tuple(getattr(args, f"{class_name}_{i}") for i in range(num_to_parse))
        

    @classmethod
    def get_help_text(cls, multiple=False):
        import contextlib
        from io import StringIO
        f = StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(f):
            _ = cls.setup("--help")
        s = f.getvalue()
        return s





ListFormattingFunction = Callable[[List[Any]], str]
ListOfListsFormattingFunction = Callable[[List[List[Any]]], str]


def format_list_using_spaces(value_list: List[Any])-> str:
    return " ".join(str(p) for p in value_list)


def format_list_using_brackets(value_list: List[Any])-> str:
    return f"[{','.join(str(p) for p in value_list)}]"


def format_list_using_single_quotes(value_list: List[Any])-> str:
    return f"'{format_list_using_spaces(value_list)}'"


def format_list_using_double_quotes(value_list: List[Any])-> str:
    return f'"{format_list_using_spaces(value_list)}"'


def format_lists_using_brackets(list_of_lists: List[List[Any]])-> str:
    return " ".join(
        format_list_using_brackets(value_list)
        for value_list in list_of_lists
    )


def format_lists_using_double_quotes(list_of_lists: List[List[Any]])-> str:
    return " ".join(
        format_list_using_double_quotes(value_list)
        for value_list in list_of_lists
    )


def format_lists_using_single_quotes(list_of_lists: List[List[Any]])-> str:
    return " ".join(
        format_list_using_single_quotes(value_list)
        for value_list in list_of_lists
    )


