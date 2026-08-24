"""
See COPYRIGHT.md for copyright information.
"""
from __future__ import annotations

from io import BytesIO, StringIO
from arelle.ModelObject import ModelObject
from typing import Any, cast, Optional, TYPE_CHECKING, Type

if TYPE_CHECKING:
    from arelle.ModelValue import QName
    from arelle.ModelXbrl import ModelXbrl

elementSubstitutionModelClass: dict[Optional[QName], Type[ModelObject]] = {}

from lxml import etree
from arelle import XbrlConst, XmlUtil
from arelle.FileSource import FileNamedTextIOWrapper
from arelle.ModelValue import qnameNsLocalName
from arelle.ModelDtsObject import (
    ModelConcept,
    ModelAttribute,
    ModelAttributeGroup,
    ModelType,
    ModelGroupDefinition,
    ModelAll,
    ModelChoice,
    ModelSequence,
    ModelAny,
    ModelAnyAttribute,
    ModelEnumeration,
    ModelRoleType,
    ModelLocator,
    ModelLink,
    ModelResource,
)

from arelle.ModelRssItem import ModelRssItem
from arelle.ModelTestcaseObject import ModelTestcaseVariation

# would be circular imports, resolve at first use after static loading
ModelDocument: Any = None
ModelFact: Any = None


def parser(
        modelXbrl: ModelXbrl,
        baseUrl: str | None,
        target: None = None,
        file: StringIO | FileNamedTextIOWrapper | None = None,
        filepath: str | None = None,
) -> tuple[etree.XMLParser[etree._Element], etree.CustomElementClassLookup, DiscoveringClassLookup]:
    _parser = etree.XMLParser(recover=True, huge_tree=True, target=target,  # type: ignore[call-overload]
                               resolve_entities=False)
    match file:
        # RSS 2.0 says no namespace.
        case StringIO():
            maybeRss = "<rss" in file.getvalue()
        case FileNamedTextIOWrapper() if isinstance(file.buffer, BytesIO):
            maybeRss = b"<rss" in file.buffer.getvalue()
            file.seek(0)
        case None:
            maybeRss = False
        case _:
            maybeRss = True
    if maybeRss:
        rssParser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
        assert file is not None
        isRss = etree.parse(file, parser=rssParser, base_url=filepath).getroot().tag == "rss"
        file.seek(0)
    else:
        isRss = False
    return setParserElementClassLookup(_parser, modelXbrl, baseUrl, isRss)


def setParserElementClassLookup(
        _parser: etree.XMLParser[etree._Element],
        modelXbrl: ModelXbrl,
        baseUrl: str | None = None,
        isRss: bool = False,
) -> tuple[etree.XMLParser[etree._Element], etree.CustomElementClassLookup, DiscoveringClassLookup]:
    nsLookup = etree.ElementNamespaceClassLookup()
    classLookup = DiscoveringClassLookup(modelXbrl, baseUrl, nsLookup=nsLookup)
    nsNameLookup: etree.CustomElementClassLookup
    if isRss:
        nsNameLookup = RssKnownNamespacesModelObjectClassLookup()
    else:
        nsNameLookup = KnownNamespacesModelObjectClassLookup(modelXbrl, fallback=classLookup, nsLookup=nsLookup)
        register_namespaces(nsLookup)
    nsLookup.set_fallback(nsNameLookup)
    _parser.set_element_class_lookup(nsLookup)
    return _parser, nsNameLookup, classLookup


LINK_LOCALNAME_TO_MODEL_CLASS = {
    "loc": ModelLocator,
    "label": ModelResource,
    "reference": ModelResource,
    "roleType": ModelRoleType,
    "arcroleType": ModelRoleType,

    "arcroleRef": ModelObject,
    "roleRef": ModelObject,
    "linkbaseRef": ModelObject,
    "linkbase": ModelObject,
} | {
    q.localName: ModelObject
    for q in [
        XbrlConst.qnLinkCalculationArc,
        XbrlConst.qnLinkDefinitionArc,
        XbrlConst.qnLinkLabelArc,
        XbrlConst.qnLinkPresentationArc,
        XbrlConst.qnLinkReferenceArc,
    ]
}


def register_namespaces(nsLookup: etree.ElementNamespaceClassLookup) -> None:
    for qname, modelClass in elementSubstitutionModelClass.items():
        assert qname is not None
        nsLookup.get_namespace(qname.namespaceURI)[qname.localName] = modelClass

    xsd = nsLookup.get_namespace(XbrlConst.xsd)
    xsd["element"] = ModelConcept
    xsd["attribute"] = ModelAttribute
    xsd["attributeGroup"] = ModelAttributeGroup
    xsd["complexType"] = ModelType
    xsd["simpleType"] = ModelType
    xsd["group"] = ModelGroupDefinition
    xsd["sequence"] = ModelSequence
    xsd["choice"] = ModelChoice
    xsd["all"] = ModelAll
    xsd["any"] = ModelAny
    xsd["anyAttribute"] = ModelAnyAttribute
    xsd["enumeration"] = ModelEnumeration

    xsd["annotation"] = ModelObject
    xsd["appinfo"] = ModelObject
    xsd["documentation"] = ModelObject
    xsd["import"] = ModelObject
    xsd["schema"] = ModelObject

    link = nsLookup.get_namespace(XbrlConst.link)
    for localName, modelClass in LINK_LOCALNAME_TO_MODEL_CLASS.items():
        link[localName] = modelClass

    edgar = nsLookup.get_namespace("http://edgar/2009/conformance")
    edgar["variation"] = ModelTestcaseVariation
    edgar["testcase"] = ModelObject
    edgar[None] = ModelObject

    no_ns = nsLookup.get_namespace(None)
    no_ns["testcase"] = ModelObject
    no_ns["variation"] = ModelTestcaseVariation

    nsLookup.get_namespace("http://www.w3.org/XML/2004/xml-schema-test-suite/")["testGroup"] = ModelTestcaseVariation
    nsLookup.get_namespace("http://www.w3.org/2005/02/query-test-XQTSCatalog")["test-case"] = ModelTestcaseVariation
    nsLookup.get_namespace("http://dummy")[None] = etree.ElementBase


class KnownNamespacesModelObjectClassLookup(etree.CustomElementClassLookup):
    def __init__(self, modelXbrl: ModelXbrl, nsLookup: etree.ElementNamespaceClassLookup, fallback: etree.ElementClassLookup | None = None) -> None:
        super().__init__(fallback)
        self.modelXbrl = modelXbrl
        self.nsLookup = nsLookup

    def lookup(self, node_type: str, document: object, ns: str | None, ln: str | None) -> type[etree._Element] | None:
        # node_type is "element", "comment", "PI", or "entity"
        if node_type == "element":
            assert ln is not None, "element nodes must have a local name"
            result: type[ModelObject] | None
            if ln == "testcase" and ns is not None and ns.startswith("http://xbrl.org/"):
                result = ModelObject
            elif ln == "variation" and ns is not None and ns.startswith("http://xbrl.org/"):
                result = ModelTestcaseVariation
            else:
                # match specific element types or substitution groups for types
                result = self.modelXbrl.matchSubstitutionGroup(qnameNsLocalName(ns, ln), elementSubstitutionModelClass)
            if result is not None:
                self.nsLookup.get_namespace(ns)[ln] = result
            return result
        elif node_type == "comment":
            from arelle.ModelObject import ModelComment

            return ModelComment
        elif node_type == "PI":
            return etree.PIBase
        elif node_type == "entity":
            return etree.EntityBase
        # returning None delegates to fallback lookup classes
        return None


class RssKnownNamespacesModelObjectClassLookup(etree.CustomElementClassLookup):
    def lookup(self, node_type: str, document: object, ns: str | None, ln: str | None) -> type[etree._Element] | None:
        match node_type:
            case "element":
                return ModelRssItem if ln == "item" else ModelObject
            case "comment":
                from arelle.ModelObject import ModelComment
                return ModelComment
            case "PI":
                return etree.PIBase
            case "entity":
                return etree.EntityBase
        return None


class DiscoveringClassLookup(etree.PythonElementClassLookup):
    def __init__(self, modelXbrl: ModelXbrl, baseUrl: str | None, nsLookup: etree.ElementNamespaceClassLookup, fallback: etree.ElementClassLookup | None = None) -> None:
        super().__init__(fallback)
        self.modelXbrl = modelXbrl
        self.nsLookup = nsLookup
        self.streamingOrSkipDTS = modelXbrl.skipDTS or getattr(modelXbrl, "isStreamingMode", False)
        self.baseUrl = baseUrl
        self.discoveryAttempts: set[str] = set()
        global ModelFact, ModelDocument
        if ModelDocument is None:
            from arelle import ModelDocument
        if self.streamingOrSkipDTS and ModelFact is None:
            from arelle.ModelInstanceObject import ModelFact

    def lookup(self, document: object, proxyElement: etree._Element) -> type[etree._Element] | None:
        # check if proxyElement's namespace is not known
        ns: str | None
        tag = cast(str, proxyElement.tag)
        ns, sep, ln = tag.partition("}")
        if sep:
            ns = ns[1:]
        else:
            ln = ns
            ns = None
        if (ns and
            ns not in self.discoveryAttempts and
            ns not in self.modelXbrl.namespaceDocs):
            # is schema loadable?  requires a schemaLocation
            relativeUrl = XmlUtil.schemaLocation(proxyElement, ns)
            self.discoveryAttempts.add(ns)
            if relativeUrl:
                ModelDocument.loadSchemalocatedSchema(self.modelXbrl, proxyElement, relativeUrl, ns, self.baseUrl)

        modelObjectClass = self.modelXbrl.matchSubstitutionGroup(
            qnameNsLocalName(ns, ln), elementSubstitutionModelClass
        )

        result = ModelObject
        if modelObjectClass is not None:
            result = modelObjectClass
        elif self.streamingOrSkipDTS and ns not in (XbrlConst.xbrli, XbrlConst.link):
            # self.makeelementParentModelObject is set in streamingExtensions.py and ModelXbrl.createFact
            ancestor = proxyElement.getparent() or getattr(self.modelXbrl, "makeelementParentModelObject", None)
            while ancestor is not None:
                ancestorTag = cast(str, ancestor.tag)  # not a modelObject yet, just parser prototype
                if ancestorTag.startswith("{http://www.xbrl.org/2003/instance}") or ancestorTag.startswith("{http://www.xbrl.org/2003/linkbase}"):
                    if ancestorTag == "{http://www.xbrl.org/2003/instance}xbrl":
                        # element not parented by context or footnoteLink
                        return ModelFact  # type: ignore[no-any-return]
                    else:
                        break  # cannot be a fact
                ancestor = ancestor.getparent()

        xlinkType = proxyElement.get("{http://www.w3.org/1999/xlink}type")
        if xlinkType == "extended":
            return ModelLink
        elif xlinkType == "locator":
            return ModelLocator
        elif xlinkType == "resource":
            return ModelResource

        self.nsLookup.get_namespace(ns)[ln] = result
        return result
