import argparse
import collections
import dataclasses
import enum
import inspect
import logging
from enum import Enum
from typing import *
from typing import cast

from .. import docstring, utils
from ..utils import Dataclass, DataclassType
from ..helpers import dict_field
from .wrapper import Wrapper

logger = logging.getLogger(__name__)


class FieldWrapper(Wrapper[dataclasses.Field]):
    """
    The FieldWrapper class acts a bit like an 'argparse.Action' class, which
    essentially just creates the `option_strings` and `arg_options` that get
    passed to the `add_argument(*option_strings, **arg_options)` function of the
    `argparse._ArgumentGroup` (in this case represented by the `parent`
    attribute, an instance of the class `DataclassWrapper`).

    The `option_strings`, `required`, `help`, `metavar`, `default`, etc.
    attributes just autogenerate the argument of the same name of the 
    above-mentioned `add_argument` function. The `arg_options` attribute fills 
    in the rest and may overwrite these values, depending on the type of field. 

    The `field` argument is the actually wrapped `dataclasses.Field` instance.
    """
    
    # Wether or not `simple_parsing` should add option_string variants where
    # underscores in attribute names are replaced with dashes.
    # For example, when set to `True`, "--no-cache" and "--no_cache" could both
    # be used to point to the same attribute `no_cache` on some dataclass.
    # TODO: This can often make "--help" messages a bit crowded
    add_dash_variants: ClassVar[bool] = False


    def __init__(self, field: dataclasses.Field, parent: Any = None):
        # super().__init__(wrapped=field, name=field.name)
        self.field: dataclasses.Field = field
        self._parent: Any = parent
        # Holders used to 'cache' the properties.
        # (could've used cached_property with Python 3.8).
        self._option_strings: Optional[Set[str]] = None
        self._required: Optional[bool] = None
        self._docstring: docstring.AttributeDocString = docstring.AttributeDocString()
        self._help: Optional[str] = None
        self._metavar: Optional[str] = None
        self._default: Optional[Union[Any, List[Any]]] = None
        self._dest: Optional[str] = None
        # the argparse-related options:
        self._arg_options: Dict[str, Any] = {}
        self._dest_field: Optional["FieldWrapper"] = None


        # stores the resulting values for each of the destination attributes.
        self._results: Dict[str, Any] = {}


    @property
    def arg_options(self) -> Dict[str, Any]:
        """Dictionary of values to be passed to the `add_argument` method.

        The main feature of this package is to infer these arguments 
        automatically using features of the built-in `dataclasses` package, as
        well as Python's type annotations.

        By passing additional keyword arguments to the `field()`
        function, the autogenerated arguments can be overwriten,
        giving access to all of the usual argparse features know and love.

        NOTE: When passing an `action` keyword argument, we remove all the
        autogenerated options that aren't required by the Action class
        constructor.
        For example, when specifying a custom `action` like "store_true" or
        "store_false", the `type` argument autogenerated here shouldn't be
        passed to the constructor of the `argparse._StoreFalseAction`, so we
        discard it.
        """
        if self._arg_options:
            return self._arg_options
        # get the auto-generated options.
        options = self.get_arg_options()
        # overwrite the auto-generated options with given ones, if any.
        options.update(self.custom_arg_options)
        # only keep the arguments used by the Action constructor.
        action = options.get("action", "store")
        self._arg_options = only_keep_action_args(options, action)
        return self._arg_options

    def __call__(self,
                 parser: argparse.ArgumentParser,
                 namespace: argparse.Namespace,
                 values: Any,
                 option_string: Optional[str] = None):
        """Immitates a custom Action, which sets the corresponding value from
        `values` at the right destination in the `constructor_arguments` of the
        parser.

        TODO: Could be simplified by removing unused arguments, if we decide
        that there is no real value in implementing a CustomAction class.

        Args:
            parser (argparse.ArgumentParser): the `simple_parsing.ArgumentParser` used.
            namespace (argparse.Namespace): (unused).
            values (Any): The parsed values for the argument.
            option_string (Optional[str], optional): (unused). Defaults to None.
        """
        from simple_parsing import ArgumentParser
        parser = cast(ArgumentParser, parser)

        if self.is_reused:
            values = self.duplicate_if_needed(values)
            logger.debug(f"(replicated the parsed values: '{values}')")
        else:
            values = [values]

        self._results = {}

        for destination, value in zip(self.destinations, values):
            parent_dest, attribute = utils.split_dest(destination)
            value = self.postprocess(value)
            self._results[destination] = value
            parser.constructor_arguments[parent_dest][attribute] = value
            logger.debug(f"setting value of {value} in constructor arguments "
                         f"of parent at key '{parent_dest}' and attribute "
                         f"'{attribute}'")

    def get_arg_options(self) -> Dict[str, Any]:
        if not self.field.init:
            return {}

        _arg_options: Dict[str, Any] = {}
        # TODO: should we explicitly use `str` whenever the type isn't a builtin
        # type? or try to use it as a constructor?
        _arg_options["type"] = self.type
        _arg_options["help"] = self.help
        _arg_options["required"] = self.required
        _arg_options["dest"] = self.dest
        _arg_options["default"] = self.default

        if self.is_enum:
            # we actually parse enums as string, and convert them back to enums
            # in the `process` method.
            _arg_options["choices"] = list(e.name for e in self.type)
            _arg_options["type"] = str
            # if the default value is an Enum, we convert it to a string.
            if self.default:
                def enum_to_str(e): return e.name if isinstance(e, Enum) else e
                if self.is_reused:
                    _arg_options["default"] = [enum_to_str(
                        default) for default in self.default]
                else:
                    _arg_options["default"] = enum_to_str(self.default)

        elif self.is_list:
            # Check if typing.List or typing.Tuple was used as an annotation, in
            # which case we can automatically convert items to the desired item
            # type.
            T = utils.get_argparse_type_for_container(self.type)
            logger.debug(
                f"Adding a List attribute '{self.name}'"
                f"with items of type '{T}'"
            )
            _arg_options["nargs"] = "*"
            _arg_options["type"] = T

            if self.is_reused:
                type_fn = utils._parse_multiple_containers(self.type)
                type_fn.__name__ = utils.get_type_name(self.type)
                _arg_options["type"] = type_fn

        elif self.is_tuple:
            T = utils.get_argparse_type_for_container(self.type)
            logging.debug(
                f"Adding a Tuple attribute '{self.name}' "
                f"with items of type '{T}'"
            )
            _arg_options["nargs"] = utils.get_container_nargs(self.type)
            _arg_options["type"] = utils._parse_container(self.type)

            if self.is_reused:
                type_fn = utils._parse_multiple_containers(self.type)
                type_fn.__name__ = utils.get_type_name(self.type)
                _arg_options["type"] = type_fn

        elif self.is_bool:
            _arg_options["type"] = utils.str2bool
            _arg_options["type"].__name__ = "bool"
            # if self.default is not None:
            _arg_options["nargs"] = "?"

        if self.is_reused:
            if self.required:
                _arg_options["nargs"] = "+"
            else:
                _arg_options["nargs"] = "*"

        return _arg_options

    def duplicate_if_needed(self, parsed_values: Any) -> List[Any]:
        """Duplicates the passed argument values if needed, such that each instance gets a value.

        For example, if we expected 3 values for an argument, and a single value was passed,
        then we duplicate it so that each of the three instances get the same value.

        Args:
            parsed_values (Any): The parsed value(s)

        Raises:
            utils.InconsistentArgumentError: If the number of arguments passed is inconsistent (neither 1 or the number of instances)

        Returns:
            List[Any]: The list of parsed values, of the right length.
        """
        num_instances_to_parse = len(self.destinations)
        logger.debug(f"num to parse: {num_instances_to_parse}")
        logger.debug(f"(raw) parsed values: '{parsed_values}'")

        assert self.is_reused
        assert num_instances_to_parse > 1, "multiple is true but we're expected to instantiate only one instance"

        if utils.is_list(self.type) and isinstance(parsed_values, tuple):
            parsed_values = list(parsed_values)

        if not self.is_tuple and not self.is_list and isinstance(parsed_values, list):
            nesting_level = utils.get_nesting_level(parsed_values)
            if (
                nesting_level == 2 and len(parsed_values) == 1 and
                len(parsed_values[0]) == num_instances_to_parse
            ):
                return parsed_values[0]

        if not isinstance(parsed_values, (list, tuple)):
            parsed_values = [parsed_values]

        if len(parsed_values) == num_instances_to_parse:
            return parsed_values
        elif len(parsed_values) == 1:
            return parsed_values * num_instances_to_parse
        else:
            raise utils.InconsistentArgumentError(
                f"The field '{self.name}' contains {len(parsed_values)} values,"
                f" but either 1 or {num_instances_to_parse} values were "
                f"expected."
            )
        return parsed_values

    def postprocess(self, raw_parsed_value: Any) -> Any:
        """Applies any conversions to the 'raw' parsed value before it is used
        in the constructor of the dataclass.

        Args:
            raw_parsed_value (Any): The 'raw' parsed value.

        Returns:
            Any: The processed value
        """
        if self.is_enum:
            logger.debug(
                f"field postprocessing for Enum field '{self.name}' with value:"
                f" {raw_parsed_value}'"
            )
            if isinstance(raw_parsed_value, str):
                raw_parsed_value = self.type[raw_parsed_value]
            return raw_parsed_value

        elif self.is_tuple:
            # argparse always returns lists by default. If the field was of a
            # Tuple type, we just transform the list to a Tuple.
            if not isinstance(raw_parsed_value, tuple):
                return tuple(raw_parsed_value)

        elif self.is_bool:
            # print(self.name, raw_parsed_value)
            if self.dest_field:
                other_default = self.dest_field.field.metadata.get("_original_default")
                # print(other_default)

            if raw_parsed_value is None and self.default is not None:
                logger.debug("value is None, returning opposite of the default")
                return not self.default
            return raw_parsed_value

        elif self.is_list:
            if isinstance(raw_parsed_value, tuple):
                return list(raw_parsed_value)
            else:
                return raw_parsed_value

        elif self.is_subparser:
            return raw_parsed_value

        elif self.type not in utils.builtin_types:
            try:
                # if the field has a weird type, we try to call it directly.
                return self.type(raw_parsed_value)
            except Exception as e:
                logger.warning(
                    f"Unable to instantiate the field '{self.name}' of type "
                    f"'{self.type}' by using the type as a constructor. "
                    f"Returning the raw parsed value instead "
                    f"({raw_parsed_value}, of type {type(raw_parsed_value)}). "
                    f"(Caught Exception: {e})"
                )
                return raw_parsed_value

        logger.debug(
            f"field postprocessing for field of type '{self.type}' and with "
            f"value '{raw_parsed_value}'"
        )
        return raw_parsed_value

    @property
    def is_reused(self) -> bool:
        return len(self.destinations) > 1

    @property
    def action(self) -> Union[str, Type[argparse.Action]]:
        """The `action` argument to be passed to `add_argument(...)`."""
        return self.custom_arg_options.get("action", "store")

    @property
    def action_str(self) -> str:
        if isinstance(self.action, str):
            return self.action
        return self.action.__name__

    @property
    def custom_arg_options(self) -> Dict[str, Any]:
        """Custom argparse options that overwrite those in `arg_options`.

        Can be set by using the `field` function, passing in a keyword argument
        that would usually be passed to the parser.add_argument(
        *option_strings, **kwargs) method. 
        """
        return self.field.metadata.get("custom_args", {})

    @property
    def destinations(self) -> List[str]:
        return [
            f"{parent_dest}.{self.name}"
            for parent_dest in self.parent.destinations
        ]

    @property
    def option_strings(self) -> List[str]:
        """Generates the `option_strings` argument to the `add_argument` call. 

        `parser.add_argument(*name_or_flags, **arg_options)`

        ## Notes:
        - Additional names for the same argument can be added via the `field`
        function.
        - Whenever the name of an attribute includes underscores ("_"), the same
        argument can be passed by using dashes ("-") instead. This also includes
        aliases.
        - If an alias contained leading dashes, either single or double, the
        same number of dashes will be used, even in the case where a prefix is 
        added.

        For an illustration of this, see the aliases example.

        """

        dashes:  List[str] = []  # contains the leading dashes.
        options: List[str] = []  # contains the name following the dashes.

        dash = "-" if len(self.name) == 1 else "--"
        option = f"{self.prefix}{self.name}"

        dashes.append(dash)
        options.append(option)

        if dash == "-":
            # also add a double-dash option:
            dashes.append("--")
            options.append(option)

        # add all the aliases that were passed to the `field` function.
        for alias in self.aliases:
            if alias.startswith("--"):
                dash = "--"
                name = alias[2:]
            elif alias.startswith("-"):
                dash = "-"
                name = alias[1:]
            else:
                dash = "-" if len(alias) == 1 else "--"
                name = alias
            option = f"{self.prefix}{name}"

            dashes.append(dash)
            options.append(option)

        # Additionally, add all name variants with the "_" replaced with "-".
        # For example, "--no-cache" will correctly set the `no_cache` attribute,
        # even if an alias isn't explicitly created.

        if FieldWrapper.add_dash_variants:
            additional_options = [
                option.replace("_", "-")
                for option in options if "_" in option
            ]
            additional_dashes = [
                "-" if len(option) == 1 else "--"
                for option in additional_options
            ]
            options.extend(additional_options)
            dashes.extend(additional_dashes)
        # remove duplicates by creating a set.
        option_strings = set(
            f"{dash}{option}" for dash, option in zip(dashes, options)
        )
        # TODO: possibly sort the option strings, if argparse doesn't do it
        # already.
        return list(sorted(option_strings, key=len))

    @property
    def prefix(self) -> str:
        return self.parent.prefix

    @property
    def aliases(self) -> List[str]:
        return self.field.metadata.get("alias", [])

    @property
    def dest(self) -> str:
        """Where the attribute will be stored in the Namespace."""
        self._dest = super().dest
        # TODO: If a custom `dest` was passed, and it is a `Field` instance,
        # find the corresponding FieldWrapper and use its `dest` instead of ours.
        if self.dest_field:
            self._dest = self.dest_field.dest
            self.custom_arg_options.pop("dest", None)
        return self._dest

    @property
    def is_proxy(self) -> bool:
        return self.dest_field is not None

    @property
    def dest_field(self) -> Optional["FieldWrapper"]:
        """ Return the `FieldWrapper` for which `self` is a proxy (same dest).
        When a `dest` argument is passed to `field()`, and its value is a
        `Field`, that indicates that this Field is just a proxy for another.

        In such a case, we replace the dest of `self` with that of the other
        wrapper's we then find the corresponding FieldWrapper and use its `dest`
        instead of ours.
        """
        if self._dest_field is not None:
            return self._dest_field
        custom_dest = self.custom_arg_options.get("dest")
        if isinstance(custom_dest, dataclasses.Field):
            all_fields: List[FieldWrapper] = []
            for parent in self.lineage():
                all_fields.extend(parent.fields)  # type: ignore
            for other_wrapper in all_fields:
                if custom_dest is other_wrapper.field:
                    self._dest_field = other_wrapper
                    break
        return self._dest_field


    @property
    def nargs(self):
        return self.custom_arg_options.get("nargs", None)

    # @property
    # def const(self):
    #     return self.custom_arg_options.get("const", None)


    @property
    def default(self) -> Any:
        """ Either a single default value, when parsing a single argument, or
        the list of default values, when this argument is reused multiple times
        (which only happens with the `ConflictResolution.ALWAYS_MERGE` option).

        In order of increasing priority, this could either be:
        1. The default attribute of the field
        2. the value of the corresponding attribute on the parent,
        if it has a default value
        """
        if self._default is not None:
            return self._default

        default: Any = utils.default_value(self.field)

        if default is dataclasses.MISSING:
            default = None

        if self.action == "store_true" and default is None:
            default = False
        if self.action == "store_false" and default is None:
            default = True

        if self.parent.defaults:
            # if the dataclass holding this field has a default value (either
            # when passed  manually or by nesting), use the corresponding
            # attribute on that default instance.
            defaults = []
            for default_dataclass_instance in self.parent.defaults:
                parent_value = getattr(default_dataclass_instance, self.name)
                defaults.append(parent_value)
            default = defaults[0] if len(defaults) == 1 else defaults

        if self.is_reused and default is not None:
            n_destinations = len(self.destinations)
            assert n_destinations >= 1
            if not isinstance(default, list) or len(default) != n_destinations:
                default = [default] * n_destinations
            assert len(default) == n_destinations, (
                f"Not the same number of default values and destinations. "
                f"(default: {default}, # of destinations: {n_destinations})"
            )

        self._default = default
        return self._default

    @default.setter
    def default(self, value: Any):
        self._default = value

    @property
    def required(self) -> bool:
        if self._required is not None:
            return self._required

        if self.action_str.startswith("store_"):
            # all the store_* actions do not require a value.
            self._required = False
        elif self.is_optional:
            self._required = False
        elif self.parent.required:
            # if the parent dataclass is required, then this attribute is too.
            # TODO: does that make sense though?
            self._required = True

        elif self.nargs in {"?", "*"}:
            self._required = False
        elif self.nargs == "+":
            self._required = True

        elif self.default is None:
            self._required = True
        elif self.is_reused:
            # if we're reusing this argument, the default value might be a list
            # of `MISSING` values.
            self._required = any(
                v == dataclasses.MISSING for v in self.default)
        else:
            self._required = False
        return self._required

    @required.setter
    def required(self, value: bool):
        self._required = value

    @property
    def type(self):
        if utils.is_optional(self.field.type):
            type_args = set(utils.get_type_arguments(self.field.type))
            # TODO: What do we do if the type is something like Union[str, int, float]?
            if str in type_args:
                return str
            else:
                type_args.remove(type(None))
                # get the first non-NoneType type argument.
                return type_args.pop()
        return self.field.type

    @property
    def choices(self):
        return self.custom_arg_options.get("choices", None)

    @property
    def help(self) -> Optional[str]:
        if self._help:
            return self._help
        try:
            self._docstring = docstring.get_attribute_docstring(
                self.parent.dataclass,
                self.field.name
            )
        except (SystemExit, Exception) as e:
            logger.debug(
                f"Couldn't find attribute docstring for field {self.name}, {e}")
            self._docstring = docstring.AttributeDocString()

        if self._docstring.docstring_below:
            self._help = self._docstring.docstring_below
        elif self._docstring.comment_above:
            self._help = self._docstring.comment_above
        elif self._docstring.comment_inline:
            self._help = self._docstring.comment_inline
        return self._help

    @help.setter
    def help(self, value: str):
        self._help = value

    @property
    def metavar(self) -> Optional[str]:
        return self._metavar

    @metavar.setter
    def metavar(self, value: str):
        self._metavar = value

    @property
    def name(self) -> str:
        return self.field.name

    @property
    def is_list(self):
        return utils.is_list(self.type)

    @property
    def is_enum(self) -> bool:
        return utils.is_enum(self.type)

    @property
    def is_tuple(self) -> bool:
        return utils.is_tuple(self.type)

    @property
    def is_bool(self) -> bool:
        return utils.is_bool(self.type)

    @property
    def is_optional(self) -> bool:
        return utils.is_optional(self.field.type)

    @property
    def is_subparser(self) -> bool:
        return utils.is_subparser_field(self.field)

    @property
    def type_arguments(self) -> List[Type]:
        return utils.get_type_arguments(self.type)

    @property
    def parent(self) -> "simple_parsing.wrappers.dataclass_wrapper.Wrapper":
        return self._parent

    @property
    def subparsers_dict(self) -> Dict[str, Type]:
        if "subparsers" in self.field.metadata:
            return self.field.metadata["subparsers"]
        else:
            type_arguments = utils.get_type_arguments(self.type)
            return {
                dataclass_type.__name__.lower(): dataclass_type for dataclass_type in type_arguments
            }

    def add_subparsers(self, parser: argparse.ArgumentParser):
        assert self.is_subparser
        from simple_parsing import ArgumentParser  # Just for typing.
        # add subparsers for each dataclass type in the field.
        subparsers = parser.add_subparsers(
            title=self.name,
            description=self.help,
            dest=self.dest,
            parser_class=ArgumentParser
        )
        subparsers.required = True
        for subcommand, dataclass_type in self.subparsers_dict.items():
            logger.debug(f"adding subparser '{subcommand}' for type {dataclass_type}")
            subparser = subparsers.add_parser(subcommand)
            # Just for typing correctness, as we didn't explicitly change
            # the return type of subparsers.add_parser method.)
            subparser = cast(ArgumentParser, subparser)
            subparser.add_arguments(dataclass_type, dest=self.dest)

    def equivalent_argparse_code(self):
        arg_options = self.arg_options.copy()
        arg_options_string = f"{{'type': {arg_options.pop('type').__qualname__}"
        arg_options_string += str(arg_options).replace("{", ", ")
        return f"group.add_argument(*{self.option_strings}, **{arg_options_string})" 

def only_keep_action_args(options: Dict[str, Any],
                          action: Union[str, Any]) -> Dict[str, Any]:
    """Remove all the arguments in `options` that aren't required by the Action.

    Parameters
    ----------
    options : Dict[str, Any]
        A dictionary of options that would usually be passed to
        `add_arguments(*option_strings, **options)`.
    action : Union[str, Any]
        The action class or name.

    Returns
    -------
    Dict[str, Any]
        [description]
    """
    # TODO: explicitly test these custom actions?
    argparse_action_classes: Dict[str, Type[argparse.Action]] = {
        "store": argparse._StoreAction,
        "store_const": argparse._StoreConstAction,
        "store_true": argparse._StoreTrueAction,
        "store_false": argparse._StoreFalseAction,
        "append": argparse._AppendAction,
        "append_const": argparse._AppendConstAction,
        "count": argparse._CountAction,
        "help": argparse._HelpAction,
        "version": argparse._VersionAction,
        "parsers": argparse._SubParsersAction,
    }
    if action not in argparse_action_classes:
        # the provided `action` is not a standard argparse-action.
        # We don't remove any of the provided options.
        return options

    # Remove all the keys that aren't needed by the action constructor:
    action_class = argparse_action_classes[action]
    argspec = inspect.getfullargspec(action_class)

    if argspec.varargs is not None or argspec.varkw is not None:
        # if the constructor takes variable arguments, pass all the options.
        logger.debug("Constructor takes var args. returning all options.")
        return options

    args_to_keep = argspec.args + ["action"]

    kept_options, deleted_options = utils.keep_keys(options, args_to_keep)
    if deleted_options:
        logger.debug(
            f"Some auto-generated options were deleted, as they were "
            f"not required by the Action constructor: {deleted_options}."
        )
    logger.debug(f"Kept options: \t{kept_options.keys()}")
    logger.debug(f"Removed options: \t{deleted_options.keys()}")
    return kept_options
