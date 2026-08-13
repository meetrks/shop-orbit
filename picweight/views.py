"""
Views for the PicWeight listing-image standardization tool.

Access is restricted to staff: this is an operator workspace utility, not
a public-facing storefront feature.
"""

from pathlib import Path

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import SupplierImageUploadForm
from .models import SupplierImageJob
from .processing import PicWeightProcessingError, generate_listing_variants


@staff_member_required(login_url="accounts:login")
def upload(request):
    if request.method == "POST":
        form = SupplierImageUploadForm(request.POST, request.FILES)
        if form.is_valid():
            job = form.save(commit=False)
            job.uploaded_by = request.user
            job.save()

            base_filename = Path(job.source_image.name).stem
            try:
                variants = generate_listing_variants(job.source_image, base_filename)
            except PicWeightProcessingError as exc:
                job.status = SupplierImageJob.Status.FAILED
                job.error_message = str(exc)
                job.save()
                messages.error(request, str(exc))
                return redirect("picweight:upload")

            job.cyan_frame_image = variants["cyan_frame"]
            job.blue_frame_image = variants["blue_frame"]
            job.ultra_zoom_image = variants["ultra_zoom"]
            job.status = SupplierImageJob.Status.PROCESSED
            job.save()

            messages.success(request, "Your listing images are ready.")
            return redirect("picweight:result", job_id=job.pk)
    else:
        form = SupplierImageUploadForm()

    recent_jobs = SupplierImageJob.objects.filter(uploaded_by=request.user)[:10]
    return render(request, "picweight/upload.html", {"form": form, "recent_jobs": recent_jobs})


@staff_member_required(login_url="accounts:login")
def result(request, job_id):
    job = get_object_or_404(SupplierImageJob, pk=job_id, uploaded_by=request.user)
    return render(request, "picweight/result.html", {"job": job})
