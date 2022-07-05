""" alert a user to activity """
from django.db import models, transaction
from django.dispatch import receiver
from .base_model import BookWyrmModel
from . import Boost, Favorite, ImportJob, Report, Status, User

# pylint: disable=line-too-long
NotificationType = models.TextChoices(
    "NotificationType",
    "FAVORITE REPLY MENTION TAG FOLLOW FOLLOW_REQUEST BOOST IMPORT ADD REPORT INVITE ACCEPT JOIN LEAVE REMOVE GROUP_PRIVACY GROUP_NAME GROUP_DESCRIPTION",
)


class Notification(BookWyrmModel):
    """you've been tagged, liked, followed, etc"""

    user = models.ForeignKey("User", on_delete=models.CASCADE)
    read = models.BooleanField(default=False)
    notification_type = models.CharField(
        max_length=255, choices=NotificationType.choices
    )

    related_users = models.ManyToManyField(
        "User", symmetrical=False, related_name="notifications"
    )
    related_groups = models.ManyToManyField(
        "Group", symmetrical=False, related_name="notifications"
    )
    related_status = models.ForeignKey("Status", on_delete=models.CASCADE, null=True)
    related_import = models.ForeignKey("ImportJob", on_delete=models.CASCADE, null=True)
    related_list_items = models.ManyToManyField(
        "ListItem", symmetrical=False, related_name="notifications"
    )
    related_reports = models.ManyToManyField("Report", symmetrical=False)

    @classmethod
    @transaction.atomic
    def notify(cls, user, related_user, **kwargs):
        """Create a notification"""
        if not user.local or user == related_user:
            return
        notification, _ = cls.objects.get_or_create(
            user=user,
            **kwargs
        )
        notification.related_users.add(related_user)
        notification.unread = True
        notification.save()

    @classmethod
    def unnotify(cls, user, related_user, **kwargs):
        """Remove a user from a notification and delete it if that was the only user"""
        try:
            notification = cls.objects.filter(user=user, **kwargs).get()
        except Notification.DoesNotExist:
            return
        notification.related_users.remove(related_user)
        if not notification.related_users.exists():
            notification.delete()

    class Meta:
        """checks if notifcation is in enum list for valid types"""

        constraints = [
            models.CheckConstraint(
                check=models.Q(notification_type__in=NotificationType.values),
                name="notification_type_valid",
            )
        ]


@receiver(models.signals.post_save, sender=Favorite)
@transaction.atomic()
# pylint: disable=unused-argument
def notify_on_fav(sender, instance, *args, **kwargs):
    """someone liked your content, you ARE loved"""
    Notification.notify(
        instance.status.user,
        instance.user,
        related_status=instance.status,
        notification_type="FAVORITE",
    )


@receiver(models.signals.post_delete, sender=Favorite)
# pylint: disable=unused-argument
def notify_on_unfav(sender, instance, *args, **kwargs):
    """oops, didn't like that after all"""
    if not instance.status.user.local:
        return
    Notification.unnotify(
        instance.status.user,
        instance.user,
        related_status=instance.status,
        notification_type="FAVORITE"
    )


@receiver(models.signals.post_save)
@transaction.atomic
# pylint: disable=unused-argument
def notify_user_on_mention(sender, instance, *args, **kwargs):
    """creating and deleting statuses with @ mentions and replies"""
    if not issubclass(sender, Status):
        return

    if instance.deleted:
        Notification.objects.filter(related_status=instance).delete()
        return

    if (
        instance.reply_parent
        and instance.reply_parent.user != instance.user
        and instance.reply_parent.user.local
    ):
        Notification.notify(
            instance.reply_parent.user,
            instance.user,
            related_status=instance,
            notification_type="REPLY",
        )

    for mention_user in instance.mention_users.all():
        # avoid double-notifying about this status
        if not mention_user.local or (
            instance.reply_parent and mention_user == instance.reply_parent.user
        ):
            continue
        Notification.notify(
            mention_user,
            instance.user,
            notification_type="MENTION",
            related_status=instance,
        )


@receiver(models.signals.post_save, sender=Boost)
@transaction.atomic
# pylint: disable=unused-argument
def notify_user_on_boost(sender, instance, *args, **kwargs):
    """boosting a status"""
    if (
        not instance.boosted_status.user.local
        or instance.boosted_status.user == instance.user
    ):
        return

    Notification.notify(
        instance.boosted_status.user,
        instance.user,
        related_status=instance.boosted_status,
        notification_type="BOOST",
    )


@receiver(models.signals.post_delete, sender=Boost)
# pylint: disable=unused-argument
def notify_user_on_unboost(sender, instance, *args, **kwargs):
    """unboosting a status"""
    Notification.unnotify(
        instance.boosted_status.user,
        instance.user,
        related_status=instance.boosted_status,
        notification_type="BOOST",
    )


@receiver(models.signals.post_save, sender=ImportJob)
# pylint: disable=unused-argument
def notify_user_on_import_complete(
    sender, instance, *args, update_fields=None, **kwargs
):
    """we imported your books! aren't you proud of us"""
    update_fields = update_fields or []
    if not instance.complete or "complete" not in update_fields:
        return
    Notification.objects.create(
        user=instance.user,
        notification_type="IMPORT",
        related_import=instance,
    )


@receiver(models.signals.post_save, sender=Report)
@transaction.atomic
# pylint: disable=unused-argument
def notify_admins_on_report(sender, instance, *args, **kwargs):
    """something is up, make sure the admins know"""
    # moderators and superusers should be notified
    admins = User.objects.filter(
        models.Q(user_permissions__name__in=["moderate_user", "moderate_post"])
        | models.Q(is_superuser=True)
    ).all()
    for admin in admins:
        notification, _ = Notification.objects.get_or_create(
            user=admin,
            notification_type="REPORT",
        )
        notification.related_reports.add(instance)
