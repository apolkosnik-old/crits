import json

from django.conf import settings
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.http import HttpResponse
from django.core.urlresolvers import reverse
try:
    from mongoengine.base import ValidationError
except ImportError:
    from mongoengine.errors import ValidationError

from crits.campaigns.campaign import Campaign, EmbeddedTTP
from crits.campaigns.forms import TTPForm
from crits.core.class_mapper import class_from_id, class_from_type
from crits.core.crits_mongoengine import EmbeddedCampaign, json_handler
from crits.core.handlers import jtable_ajax_list, build_jtable
from crits.core.handlers import csv_export, get_item_names
from crits.core.mongo_tools import mongo_connector
from crits.core.user_tools import user_sources, is_user_subscribed
from crits.core.user_tools import is_user_favorite
from crits.notifications.handlers import remove_user_from_notification
from crits.stats.handlers import generate_campaign_stats

from crits.actors.actor import Actor
from crits.backdoors.backdoor import Backdoor
from crits.domains.domain import Domain
from crits.emails.email import Email
from crits.events.event import Event
from crits.exploits.exploit import Exploit
from crits.indicators.indicator import Indicator
from crits.ips.ip import IP
from crits.pcaps.pcap import PCAP
from crits.samples.sample import Sample
from crits.targets.handlers import get_campaign_targets
from crits.targets.target import Target

from crits.vocabulary.relationships import RelationshipTypes

# Functions for top level Campaigns.
def get_campaign_names_list(active):
    listing = get_item_names(Campaign, bool(active))
    return [c.name for c in listing]

def get_campaign_details(campaign_name, analyst):
    """
    Generate the data to render the Campaign details template.

    :param campaign_name: The name of the Campaign to get details for.
    :type campaign_name: str
    :param analyst: The user requesting this information.
    :type analyst: str
    :returns: template (str), arguments (dict)
    """

    template = None
    sources = user_sources(analyst)
    campaign_detail = Campaign.objects(name=campaign_name).first()
    if not campaign_detail:
        template = "error.html"
        args = {"error": 'No data exists for this campaign.'}
        return template, args

    ttp_form = TTPForm()

    # remove pending notifications for user
    remove_user_from_notification("%s" % analyst, campaign_detail.id, 'Campaign')

    # subscription
    subscription = {
        'type': 'Campaign',
        'id': campaign_detail.id,
        'subscribed': is_user_subscribed("%s" % analyst,
                                         'Campaign',
                                         campaign_detail.id),
    }

    #objects
    objects = campaign_detail.sort_objects()

    #relationships
    relationships = campaign_detail.sort_relationships("%s" % analyst,
                                                       meta=True)

    # relationship
    relationship = {'type': 'Campaign', 'value': campaign_detail.id}

    #comments
    comments = {'comments': campaign_detail.get_comments(),
                'url_key': campaign_name}

    #screenshots
    screenshots = campaign_detail.get_screenshots(analyst)

    # Get item counts
    formatted_query = {'campaign.name': campaign_name}
    counts = {}
    for col_obj in [Actor, Backdoor, Exploit, Sample, PCAP, Indicator, Email, Domain, IP, Event]:
        counts[col_obj._meta['crits_type']] = col_obj.objects(source__name__in=sources,
                                                              __raw__=formatted_query).count()

    # Item counts for targets
    uniq_addrs = get_campaign_targets(campaign_name, analyst)
    counts['Target'] = Target.objects(email_address__in=uniq_addrs).count()

    # favorites
    favorite = is_user_favorite("%s" % analyst, 'Campaign', campaign_detail.id)

    # analysis results
    service_results = campaign_detail.get_analysis_results()

    args = {'objects': objects,
            'relationships': relationships,
            "relationship": relationship,
            'comments': comments,
            "subscription": subscription,
            "campaign_detail": campaign_detail,
            "counts": counts,
            "favorite": favorite,
            "screenshots": screenshots,
            'service_results': service_results,
            "ttp_form": ttp_form}

    return template, args

def get_campaign_stats(campaign):
    """
    Get the statistics for this Campaign generated by mapreduce.

    :param campaign: The name of the Campaign to get stats for.
    :type campaign: str
    :returns: list of dictionaries
    """

    # The Statistics collection has a bunch of documents which are not
    # in the same format, so we can't class it at this time.
    stats = mongo_connector(settings.COL_STATISTICS)
    stat = stats.find_one({"name": "campaign_monthly"})
    data_list = []
    if stat:
        for result in stat["results"]:
            if campaign == result["campaign"] or campaign == "all":
                data = {}
                data["label"] = result["campaign"]
                data["data"] = []
                for k in sorted(result["value"].keys()):
                    data["data"].append([k, result["value"][k]])
                data_list.append(data)
    return data_list

def generate_campaign_csv(request):
    """
    Generate a CSV file of the Campaign information

    :param request: The request for this CSV.
    :type request: :class:`django.http.HttpRequest`
    :returns: :class:`django.http.HttpResponse`
    """

    response = csv_export(request, Campaign)
    return response

def generate_campaign_jtable(request, option):
    """
    Generate the jtable data for rendering in the list template.

    :param request: The request for this jtable.
    :type request: :class:`django.http.HttpRequest`
    :param option: Action to take.
    :type option: str of either 'jtlist', 'jtdelete', or 'inline'.
    :returns: :class:`django.http.HttpResponse`
    """

    refresh = request.GET.get("refresh", "no")
    if refresh == "yes":
        generate_campaign_stats()
    obj_type = Campaign
    type_ = "campaign"
    mapper = obj_type._meta['jtable_opts']
    if option == "jtlist":
        # Sets display url
        details_url = mapper['details_url']
        details_url_key = mapper['details_url_key']
        fields = mapper['fields']
        response = jtable_ajax_list(obj_type,
                                    details_url,
                                    details_url_key,
                                    request,
                                    includes=fields)
        # Ugly hack because we are the first tab in global search.
        # If there are no results for anything we will still try and
        # search campaigns since it will render that tab by default.
        # If the search parameters exclude Campaigns, we will get an
        # IGNORE. If we do, format a valid response of 0 results.
        if response['Result'] == "IGNORE":
            response = {'crits_type': 'Campaign',
                        'term': 'No Results',
                        'Records': [],
                        'TotalRecordCount': 0,
                        'Result': 'OK',
                        'msg': ''}
        return HttpResponse(json.dumps(response,
                                       default=json_handler),
                            content_type="application/json")
    # Disable campaign removal
    if option == "jtdelete":
        response = {"Result": "ERROR"}
        #if jtable_ajax_delete(obj_type,request):
        #    response = {"Result": "OK"}
        return HttpResponse(json.dumps(response,
                                       default=json_handler),
                            content_type="application/json")
    jtopts = {
        'title': "Campaigns",
        'default_sort': mapper['default_sort'],
        'listurl': reverse('crits.%ss.views.%ss_listing' % (type_, type_),
                           args=('jtlist',)),
        'searchurl': reverse(mapper['searchurl']),
        'fields': mapper['jtopts_fields'],
        'hidden_fields': mapper['hidden_fields'],
        'linked_fields': mapper['linked_fields']
    }
    jtable = build_jtable(jtopts, request)
    jtable['toolbar'] = [
        {
            'tooltip': "'All Campaigns'",
            'text': "'All'",
            'click': "function () {$('#campaign_listing').jtable('load', {'refresh': 'yes'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'New Campaigns'",
            'text': "'New'",
            'click': "function () {$('#campaign_listing').jtable('load', {'refresh': 'yes', 'status': 'New'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'In Progress Campaigns'",
            'text': "'In Progress'",
            'click': "function () {$('#campaign_listing').jtable('load', {'refresh': 'yes', 'status': 'In Progress'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'Analyzed Campaigns'",
            'text': "'Analyzed'",
            'click': "function () {$('#campaign_listing').jtable('load', {'refresh': 'yes', 'status': 'Analyzed'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'Deprecated Campaigns'",
            'text': "'Deprecated'",
            'click': "function () {$('#campaign_listing').jtable('load', {'refresh': 'yes', 'status': 'Deprecated'});}",
            'cssClass': "'jtable-toolbar-center'",
        },
        {
            'tooltip': "'Refresh campaign stats'",
            'text': "'Refresh Stats'",
            'click': "function () {$.get('" + reverse('crits.%ss.views.%ss_listing' % (type_, type_)) + "', {'refresh': 'yes'}, function () { $('#campaign_listing').jtable('reload');});}"
        },
        {
            'tooltip': "'Add Campaign'",
            'text': "'Add Campaign'",
            'click': "function () {$('#new-campaign').click()}",
        },

    ]
    # Make count fields clickable to search those listings
    for ctype in ["actor", "backdoor", "exploit", "indicator", "email",
                  "domain", "sample", "event", "ip", "pcap"]:
        url = reverse('crits.%ss.views.%ss_listing' % (ctype, ctype))
        for field in jtable['fields']:
            if field['fieldname'].startswith("'" + ctype):
                field['display'] = """ function (data) {
                return '<a href="%s?campaign='+encodeURIComponent(data.record.name)+'">'+data.record.%s_count+'</a>';
            }
            """ % (url, ctype)
    if option == "inline":
        return render_to_response("jtable.html",
                                  {'jtable': jtable,
                                   'jtid': '%s_listing' % type_,
                                   'button': '%ss_tab' % type_},
                                  RequestContext(request))
    else:
        return render_to_response("%s_listing.html" % type_,
                                  {'jtable': jtable,
                                   'jtid': '%s_listing' % type_},
                                  RequestContext(request))

def add_campaign(name, description, aliases, analyst, 
                 bucket_list=None, ticket=None, related_id=None, 
                 related_type=None, relationship_type=None):
    """
    Add a Campaign.

    :param name: The name of the new Campaign.
    :type name: str
    :param description: Description of the new Campaign.
    :type description: str
    :param aliases: Aliases for the new Campaign.
    :type aliases: str (comma separated) or list.
    :param analyst: The user adding the Campaign.
    :type analyst: str
    :param bucket_list: Buckets to add to this Campaign.
    :type bucket_list: str (comma separated) or list.
    :param ticket: Ticket(s) to add to this Campaign.
    :type ticket: str (comma separated) or list.
    :param related_id: ID of object to create relationship with
    :type related_id: str
    :param related_type: Type of object to create relationship with
    :type related_id: str
    :param relationship_type: Type of relationship to create.
    :type relationship_type: str
    :returns: dict with key 'success' (boolean) and 'message' (str).
    """

    # Verify the Campaign does not exist.
    campaign = Campaign.objects(name=name).first()
    if campaign:
        return {'success': False, 'message': ['Campaign already exists.'],
                'id': str(campaign.id)}

    # Create new campaign.
    campaign = Campaign(name=name)
    campaign.edit_description(description)

    if bucket_list:
        campaign.add_bucket_list(bucket_list, analyst)
    if ticket:
        campaign.add_ticket(ticket, analyst)

    # Adjust aliases.
    if isinstance(aliases, str):
        alias_list = aliases.split(',')
        final_aliases = [a.strip() for a in alias_list]
    elif isinstance(aliases, list):
        final_aliases = [a.strip() for a in aliases]
    else:
        final_aliases = []
    campaign.add_alias(final_aliases)

    related_obj = None
    if related_id and related_type:
        related_obj = class_from_id(related_type, related_id)
        if not related_obj:
            retVal['success'] = False
            retVal['message'] = 'Related Object not found.'
            return retVal

    campaign.save(username=analyst)

    if related_obj and relationship_type and campaign:
        relationship_type=RelationshipTypes.inverse(relationship=relationship_type)
        campaign.add_relationship(related_obj,
                              relationship_type,
                              analyst=analyst,
                              get_rels=False)
        campaign.save(username=analyst)
        campaign.reload()

    try:
        campaign.save(username=analyst)
        campaign.reload()
        return {'success': True,
                'message': 'Campaign created successfully!',
                'id': str(campaign.id)}
    except ValidationError as e:
        return {'success': False, 'message': "Invalid value: %s" % e}

def remove_campaign(name, analyst):
    """
    Remove a Campaign.

    :param name: The name of the Campaign to remove.
    :type name: str
    :param analyst: The user removing the Campaign.
    :type analyst: str
    :returns: dict with key 'success' (boolean) and 'message' (str) if failed.
    """

    campaign = Campaign.objects(name=name).first()
    if campaign:
        campaign.delete(username=analyst)
        return {'success': True}
    else:
        return {'success': False, 'message': 'Campaign not found.'}

def add_ttp(cid, ttp, analyst):
    """
    Add a TTP to a Campaign.

    :param cid: ObjectId of the Campaign.
    :type cid: str
    :param ttp: The TTP to add.
    :type ttp: str
    :param analyst: The user adding the TTP.
    :type analyst: str
    :returns: dict with keys:
              'success' (boolean),
              'campaign' (:class:`crits.campaigns.campaign.Campaign`) if success,
              'message' (str) if failed.
    """

    campaign = Campaign.objects(id=cid).first()
    if campaign:
        new_ttp = EmbeddedTTP()
        new_ttp.analyst = analyst
        new_ttp.ttp = ttp
        try:
            campaign.add_ttp(new_ttp)
            campaign.save(username=analyst)
            return {'success': True, 'campaign': campaign}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    else:
        return {'success': False, 'message': "Could not find Campaign"}

def edit_ttp(cid, old_ttp, new_ttp, analyst):
    """
    Edit an existing TTP.

    :param cid: ObjectId of the Campaign.
    :type cid: str
    :param old_ttp: Original value of the TTP.
    :type old_ttp: str
    :param new_ttp: New value of the TTP.
    :type new_ttp: str
    :param analyst: The user editing the TTP.
    :type analyst: str
    :returns: dict with key 'success' (boolean) and 'message' (str) if failed.
    """

    campaign = Campaign.objects(id=cid).first()
    if campaign:
        try:
            campaign.edit_ttp(old_ttp, new_ttp)
            campaign.save(username=analyst)
            return {'success': True}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    else:
        return {'success': False, 'message': "Could not find Campaign"}

def remove_ttp(cid, ttp, analyst):
    """
    Remove a TTP from a Campaign.

    :param cid: ObjectId of the Campaign.
    :type cid: str
    :param ttp: The TTP to remove.
    :type ttp: str
    :param analyst: The user removing the TTP.
    :type analyst: str
    :returns: dict with keys:
              'success' (boolean),
              'campaign' (:class:`crits.campaigns.campaign.Campaign`) if success,
              'message' (str) if failed.
    """

    campaign = Campaign.objects(id=cid).first()
    if campaign:
        try:
            campaign.remove_ttp(ttp)
            campaign.save(username=analyst)
            return {'success': True, 'campaign': campaign}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    else:
        return {'success': False, 'message': "Could not find Campaign"}

def modify_campaign_aliases(name, tags, analyst):
    """
    Modify the aliases for a Campaign.

    :param name: Name of the Campaign.
    :type name: str
    :param tags: The new aliases.
    :type tags: list
    :param analyst: The user setting the new aliases.
    :type analyst: str
    :returns: dict with key 'success' (boolean) and 'message' (str) if failed.
    """

    campaign = Campaign.objects(name=name).first()
    if campaign:
        campaign.set_aliases(tags)
        try:
            campaign.save(username=analyst)
            return {'success': True}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    else:
        return {'success': False}

def activate_campaign(name, analyst):
    """
    Activate a Campaign.

    :param name: Name of the Campaign.
    :type name: str
    :param analyst: The user activating the Campaign.
    :type analyst: str
    :returns: dict with key 'success' (boolean) and 'message' (str) if failed.
    """

    campaign = Campaign.objects(name=name).first()
    if campaign:
        campaign.activate()
        try:
            campaign.save(username=analyst)
            return {'success': True}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    else:
        return {'success': False}

def deactivate_campaign(name, analyst):
    """
    Deactivate a Campaign.

    :param name: Name of the Campaign.
    :type name: str
    :param analyst: The user deactivating the Campaign.
    :type analyst: str
    :returns: dict with key 'success' (boolean) and 'message' (str) if failed.
    """

    campaign = Campaign.objects(name=name).first()
    if campaign:
        campaign.deactivate()
        try:
            campaign.save(username=analyst)
            return {'success': True}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    else:
        return {'success': False}

def campaign_addto_related(crits_object, campaign, analyst):
    """
    Add this Campaign to all related top-level objects.

    :param crits_object: The top-level object to get relationships for.
    :type crits_object: class which inherits from
                    :class:`crits.core.crits_mongoengine.CritsBaseAttributes`
    :param campaign: The campaign to add to all related top-level objects.
    :type campaign: :class:`crits.core.crits_mongoengine.EmbeddedCampaign`
    :param analyst: The user adding this Campaign to the related top-level objects.
    :type analyst: str
    """

    for r in crits_object.relationships:
        klass = class_from_type(r.rel_type)
        if not klass:
            continue
        robj = klass.objects(id=str(r.object_id)).first()
        if not robj:
            continue
        robj.add_campaign(campaign)
        try:
            robj.save(username=analyst)
        except ValidationError:
            pass

# Functions for campaign attribution.
def campaign_add(campaign_name, confidence, description, related,
                 analyst, ctype=None, oid=None, obj=None, update=True):
    """
    Attribute a Campaign to a top-level object.

    :param campaign_name: The Campaign to attribute.
    :type campaign_name: str
    :param confidence: The confidence level of this attribution (low, medium, high)
    :type confidence: str
    :param description: Description of this attribution.
    :type description: str
    :param related: Should this attribution propagate to related top-level objects.
    :type related: boolean
    :param analyst: The user attributing this Campaign.
    :type analyst: str
    :param ctype: The top-level object type.
    :type ctype: str
    :param oid: The ObjectId of the top-level object.
    :type oid: str
    :param obj: The top-level object instantiated class.
    :type obj: Instantiated class object
    :param update: If True, allow merge with pre-existing campaigns
    :              If False, do not change any pre-existing campaigns
    :type update:  boolean
    :returns: dict with keys:
        'success' (boolean),
        'html' (str) if successful,
        'message' (str).
    """

    if not obj:
        if ctype and oid:
            # Verify the document exists.
            obj = class_from_id(ctype, oid)
            if not obj:
                return {'success': False, 'message': 'Cannot find %s.' % ctype}
        else:
            return {'success': False, 'message': 'Object type and ID, or object instance, must be provided.'}

    # Create the embedded campaign.
    campaign = EmbeddedCampaign(name=campaign_name, confidence=confidence, description=description, analyst=analyst)
    result = obj.add_campaign(campaign, update=update)

    if result['success']:
        if related:
            campaign_addto_related(obj, campaign, analyst)

        try:
            obj.save(username=analyst)
            html = obj.format_campaign(campaign, analyst)
            return {'success': True, 'html': html, 'message': result['message']}
        except ValidationError as e:
            return {'success': False, 'message': "Invalid value: %s" % e}
    return {'success': False, 'message': result['message']}

def campaign_edit(ctype, oid, campaign_name, confidence,
                  description, date, related, analyst):
    """
    Edit an attributed Campaign for a top-level object.

    :param ctype: The top-level object type.
    :type ctype: str
    :param oid: The ObjectId of the top-level object.
    :type oid: str
    :param campaign_name: The Campaign to attribute.
    :type campaign_name: str
    :param confidence: The confidence level of this attribution (low, medium, high)
    :type confidence: str
    :param description: Description of this attribution.
    :type description: str
    :param date: The date of attribution.
    :type date: :class:`datetime.datetime`
    :param related: Should this attribution propagate to related top-level objects.
    :type related: boolean
    :param analyst: The user editing this attribution.
    :type analyst: str
    :returns: dict with keys:
        'success' (boolean),
        'html' (str) if successful,
        'message' (str) if failed.
    """

    # Verify the document exists.
    crits_object = class_from_id(ctype, oid)
    if not crits_object:
        return {'success': False, 'message': 'Cannot find %s.' % ctype}

    # Create the embedded campaign.
    campaign = EmbeddedCampaign(name=campaign_name, confidence=confidence,
                                description=description, analyst=analyst,
                                date=date)
    crits_object.edit_campaign(campaign_item=campaign)

    if related:
        campaign_addto_related(crits_object, campaign, analyst)

    try:
        crits_object.save(username=analyst)
        html = crits_object.format_campaign(campaign, analyst)
        return {'success': True, 'html': html}
    except ValidationError as e:
        return {'success': False, 'message': "Invalid value: %s" % e}

def campaign_remove(ctype, oid, campaign, analyst):
    """
    Remove Campaign attribution.

    :param ctype: The top-level object type.
    :type ctype: str
    :param oid: The ObjectId of the top-level object.
    :type oid: str
    :param campaign: The Campaign to remove.
    :type campaign: str
    :param analyst: The user removing this attribution.
    :type analyst: str
    :returns: dict with key 'success' (boolean) and 'message' (str) if failed.

    """

    # Verify the document exists.
    crits_object = class_from_id(ctype, oid)
    if not crits_object:
        return {'success': False, 'message': 'Cannot find %s.' % ctype}

    crits_object.remove_campaign(campaign)
    try:
        crits_object.save(username=analyst)
        return {'success': True}
    except ValidationError as e:
        return {'success': False, 'message': "Invalid value: %s" % e}
