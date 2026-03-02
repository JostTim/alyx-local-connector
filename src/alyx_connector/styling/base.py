from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any, Callable, Mapping, Optional, Tuple, TypeVar, get_args, get_origin
import uuid
import inspect

# -----------------------------
# Core style primitives
# -----------------------------

StylerClass = TypeVar("StylerClass", bound="BaseStyler")

NO_ESCAPE_FLAG = ":noescape:"


def noescape(string: str):
    return NO_ESCAPE_FLAG + string


def obfuscate(string: str, max_length: int = 16):
    """Obfuscates a given string by replacing part of it with asterisks.

    Args:
        string (str): The string to be obfuscated. Must be a valid string.
        max_length (int, optional): The maximum length of the obfuscated portion.
            Defaults to 16.

    Raises:
        ValueError: If the input is not a string.

    Returns:
        str: The obfuscated string, where part of the original string is replaced
        with asterisks if its length exceeds 6 characters. If the string length
        is 6 or less, it returns a string of asterisks of the same length.
    """

    if not isinstance(string, str):
        raise ValueError("obfuscated object must be a string")
    if len(string) > 6:
        return string[:4] + ("*" * min(max_length, (len(string) - 4)))
    return "*" * len(string)


@dataclass(frozen=True)
class Style:
    """
    A small, composable style object.
    - props: CSS properties for inline style (e.g. {"color": "red"}).
    - classes: CSS classes to add to the element.
    - attrs: arbitrary HTML attributes (e.g. {"title": "..."})
    """

    props: Mapping[str, str] = field(default_factory=dict)
    classes: Tuple[str, ...] = ()
    attrs: Mapping[str, str] = field(default_factory=dict)

    def then(self, other: "Style") -> "Style":
        """Compose styles (other has prio over self CSS props/attrs)."""
        merged_css = dict(self.props)
        merged_css.update(other.props)
        merged_classes = tuple(set(self.classes).union(set(other.classes)))
        merged_attrs = {**self.attrs, **other.attrs}
        return Style(props=merged_css, classes=merged_classes, attrs=merged_attrs)

    def add(
        self,
        props: Optional[Mapping[str, str]] = None,
        classes: Optional[Tuple[str, ...]] = None,
        attrs: Optional[Mapping[str, str]] = None,
    ) -> "Style":
        if props is None:
            props = {}
        if classes is None:
            classes = ()
        if attrs is None:
            attrs = {}
        return self.then(Style(props, classes, attrs))

    def add_cls(self, *classes: str) -> "Style":
        return self.add(classes=classes)

    def add_css(self, **props: str) -> "Style":
        return self.add(props=props)

    def add_attr(self, **attrs: str) -> "Style":
        return self.add(attrs=attrs)

    @staticmethod
    def css(**props: str) -> "Style":
        return Style(props=props)

    @staticmethod
    def cls(*classes: str) -> "Style":
        return Style(classes=tuple(c for c in classes if c))

    @staticmethod
    def attr(**attrs: str) -> "Style":
        return Style(attrs=attrs)

    @staticmethod
    def _css_dict_to_inline(css: Mapping[str, str]) -> str:
        if not css:
            return ""
        # sorted for stable order and reproducible HTML
        items = ";\n".join(f"{k}: {v}" for k, v in sorted(css.items()))
        return items + ";"

    def style_to_attrs(self) -> Mapping[str, str]:
        attrs = dict(self.attrs)
        if self.classes:
            attrs["class"] = " ".join(self.classes)
        if self.props:
            attrs["style"] = self._css_dict_to_inline(self.props)
        return attrs

    def __str__(self) -> str:
        attrs = self.style_to_attrs()
        if not attrs:
            return ""
        return " " + " ".join(f'{k}="{escape(str(v), quote=True)}"' for k, v in attrs.items())

    def to_str(self) -> str:
        return self.__str__()


@dataclass(frozen=True)
class Element:
    tag: str
    style: "Style" = field(default_factory=Style)
    content: "list[Element | str]" = field(default_factory=list)

    def add_content(self, *element: "Element | str") -> "Element":
        self.content.extend(element)
        return self

    def render(self) -> list[str]:
        html = []
        if self.tag:
            html.append(f"<{self.tag}{self.style.to_str()}>")
        for content in self.content:
            if not isinstance(content, Element):
                content = str(content)
                content = (
                    content.replace(NO_ESCAPE_FLAG, "")
                    if content.startswith(NO_ESCAPE_FLAG)
                    else escape(content)
                )
                html.append(content)
            else:
                html.extend(content.render())
        if self.tag:
            html.append(f"</{self.tag}>")
        return html

    def __str__(self) -> str:
        return "".join(self.render())


# -----------------------------
# Styling rules (extensible)
# -----------------------------


class Rule:
    """Base class for rules that can contribute styles to elements.
    They use a function provided as argument to decide to return a
    blank Style or a Style with added features, depending on the function execution"""

    rule_type: str
    fn: Callable[[Any], Style]

    def style_conditionnaly(self, rule_type: str, *args, **kwargs) -> Style:
        if rule_type != self.rule_type:
            return Style()
        self.verify_fn_signature()
        return self.fn(*args, **kwargs)

    def verify_fn_signature(self) -> None:
        """
        Verifies that `self.fn` matches the annotation of `Rule.fn`
        (e.g. Callable[[Any], Style]):

        - must be callable
        - must accept at least 1 positional argument (the Any)
        - must not require more than 1 positional argument
          (extra args must be optional or via *args/**kwargs)
        - return annotation (if present) must be compatible with Style
        """
        if not callable(self.fn):
            raise TypeError(f"Rule.fn must be callable, got {type(self.fn)!r}")

        # expected = type(self).fn  # the annotated attribute on the class
        # expected_type = getattr(expected, "__annotations__", None)

        # Pull expected Callable signature from the class annotation
        ann = getattr(type(self), "__annotations__", {}).get("fn", None)
        if ann is None:
            raise TypeError("Rule.fn has no type annotation to verify against")

        origin = get_origin(ann)
        if origin is not Callable:
            raise TypeError(f"Rule.fn annotation must be Callable[..., ...], got {ann!r}")

        arg_types, ret_type = get_args(ann)
        # arg_types is either [...] or Ellipsis
        if arg_types is Ellipsis:
            raise TypeError("Rule.fn annotation uses Callable[..., R]; cannot verify arity")

        expected_arity = len(arg_types)

        sig = inspect.signature(self.fn)
        params = list(sig.parameters.values())

        # Count required positional-or-keyword / positional-only params
        required_pos = [
            p
            for p in params
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
        ]

        # Count positional-capable params (excluding *args)
        positional_capable = [
            p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]

        has_varargs = any(p.kind is p.VAR_POSITIONAL for p in params)

        # Must be able to accept at least expected_arity positional args
        if len(positional_capable) + (10**9 if has_varargs else 0) < expected_arity:
            raise TypeError(
                f"{self.fn!r} cannot accept {expected_arity} positional argument(s); "
                f"signature is {sig}"
            )

        # Must not require more than expected_arity positional args
        if len(required_pos) > expected_arity:
            raise TypeError(
                f"{self.fn!r} requires {len(required_pos)} positional argument(s), "
                f"expected {expected_arity}; signature is {sig}"
            )

        # Return type check (only if function has an annotation)
        fn_ret = sig.return_annotation
        if fn_ret is not inspect._empty and ret_type is not Any:
            # Basic compatibility check: exact match or subclass
            try:
                if fn_ret is not ret_type and not (
                    isinstance(fn_ret, type)
                    and isinstance(ret_type, type)
                    and issubclass(fn_ret, ret_type)
                ):
                    raise TypeError(
                        f"{self.fn!r} return annotation {fn_ret!r} does not match expected {ret_type!r}"
                    )
            except TypeError:
                # If annotations are not classes (e.g. typing constructs), fall back to equality
                if fn_ret != ret_type:
                    raise TypeError(
                        f"{self.fn!r} return annotation {fn_ret!r} does not match expected {ret_type!r}"
                    )


# -----------------------------
# Base styler
# -----------------------------


class BaseStyler:
    """
    Minimal, pandas-Styler-like API:
      - chainable methods that register rules
      - render() produces HTML
    Data model:
      - rows: list of dict-like rows
      - columns: explicit column order
    """

    _unique_id: str

    def __init__(self):
        self._unique_id = str(uuid.uuid4())
        self._rules = []

    # ---- style resolution ----

    def style_from_rules(self, rule_type: str, *args, **kwargs) -> Style:
        style = Style()
        for rule in self._rules:
            style = style.then(rule.style_conditionnaly(rule_type, *args, **kwargs))
        return style

    # ---- chainable API ----

    def add_rule(self: StylerClass, rule: Rule) -> StylerClass:
        self._rules.append(rule)
        return self

    # ---- rendering ----

    def compose(self) -> Element:
        root = Element("")
        return root

    def _finalize_compose(self, composed_root: Element) -> Element:
        composed_root.add_content(
            Element(
                "style", content=[noescape(self.css.replace("{{ unique_id }}", self._unique_id))]
            )
        )
        composed_root.add_content(
            Element(
                "script", content=[noescape(self.js.replace("{{ unique_id }}", self._unique_id))]
            )
        )
        return composed_root

    def to_html(self) -> str:
        return "".join(self._finalize_compose(self.compose()).render())

    def __str__(self) -> str:
        return self.to_html()

    def _repr_html_(self) -> str:
        return self.to_html()

    @property
    def css(self) -> str:
        css = ""
        return css

    @property
    def js(self) -> str:
        js = ""
        return js
