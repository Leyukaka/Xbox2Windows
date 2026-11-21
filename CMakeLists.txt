cmake_minimum_required(VERSION 3.21)
project(Xbox2Windows LANGUAGES CXX)

enable_testing()
add_executable(root_probe tools/root_probe.cpp)
target_compile_features(root_probe PRIVATE cxx_std_17)
target_link_libraries(root_probe PRIVATE d3d12)
add_executable(root_device_probe tools/root_device_probe.cpp)
target_compile_features(root_device_probe PRIVATE cxx_std_17)
target_link_libraries(root_device_probe PRIVATE d3d12 dxgi)
add_executable(d3d12_sdk_check tests/d3d12_sdk_check.cpp)
target_compile_features(d3d12_sdk_check PRIVATE cxx_std_17)
target_link_libraries(d3d12_sdk_check PRIVATE d3d12)
add_test(NAME desktop_root_signature COMMAND d3d12_sdk_check)
add_test(NAME desktop_bvh_root_flags COMMAND d3d12_sdk_check bvh-flags)

add_executable(era_heap_check tests/era_heap_check.cpp src/era_heap.cpp)
target_include_directories(era_heap_check PRIVATE src)
target_compile_features(era_heap_check PRIVATE cxx_std_20)
foreach(scenario IN ITEMS allocation failure concurrency caller_layout reuse observed_words)
    add_test(NAME era_heap_${scenario} COMMAND era_heap_check ${scenario})
endforeach()

add_executable(memory_query_check tests/memory_query_check.cpp)
target_compile_features(memory_query_check PRIVATE cxx_std_17)
target_link_libraries(memory_query_check PRIVATE psapi)
add_test(NAME desktop_memory_query COMMAND memory_query_check)

add_executable(era_query_check tests/era_query_check.cpp src/era_query.cpp)
target_include_directories(era_query_check PRIVATE src)
target_compile_features(era_query_check PRIVATE cxx_std_17)
foreach(scenario IN ITEMS fields tail registry short)
    add_test(NAME era_query_${scenario} COMMAND era_query_check ${scenario})
endforeach()

find_program(DXC_EXECUTABLE dxc REQUIRED)
get_filename_component(DXC_BIN_DIR "${DXC_EXECUTABLE}" DIRECTORY)
target_compile_definitions(root_probe PRIVATE SDK_DXC_DIR=L"${DXC_BIN_DIR}/")
add_executable(offscreen_check tests/offscreen_check.cpp tests/compute_check.cpp tests/dxr_check.cpp tests/buffer_ops.cpp)
target_compile_features(offscreen_check PRIVATE cxx_std_17)
target_link_libraries(offscreen_check PRIVATE d3d12 dxgi dxguid)
target_include_directories(offscreen_check PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
set(OFFSCREEN_AGILITY_620_DIR "" CACHE PATH "Agility SDK 1.620.2 x64 DLL directory for debug validation")
if(OFFSCREEN_AGILITY_620_DIR)
    if(EXISTS "${OFFSCREEN_AGILITY_620_DIR}/sdk.nupkg")
        set(agility_headers "${CMAKE_CURRENT_BINARY_DIR}/agility-headers")
        file(ARCHIVE_EXTRACT INPUT "${OFFSCREEN_AGILITY_620_DIR}/sdk.nupkg" DESTINATION "${agility_headers}"
             PATTERNS "build/native/include/*.h")
        target_include_directories(offscreen_check BEFORE PRIVATE "${agility_headers}/build/native/include")
    endif()
    foreach(dll IN ITEMS D3D12Core.dll d3d12SDKLayers.dll)
        configure_file("${OFFSCREEN_AGILITY_620_DIR}/${dll}" "${CMAKE_CURRENT_BINARY_DIR}/D3D12/${dll}" COPYONLY)
    endforeach()
    target_compile_definitions(offscreen_check PRIVATE OFFSCREEN_AGILITY_620)
endif()
foreach(stage IN ITEMS vs ps)
    string(TOUPPER "${stage}" entry)
    set(header "${CMAKE_CURRENT_BINARY_DIR}/triangle_${stage}.h")
    set(binary "${CMAKE_CURRENT_BINARY_DIR}/triangle_${stage}.cso")
    add_custom_command(OUTPUT "${header}" "${binary}"
        COMMAND "${DXC_EXECUTABLE}" -T ${stage}_6_0 -E ${entry}Main -Vn triangle_${stage}
                -Fh "${header}" -Fo "${binary}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/triangle.hlsl"
        DEPENDS tests/triangle.hlsl VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
endforeach()
set(ray_header "${CMAKE_CURRENT_BINARY_DIR}/ray_query.h")
set(pixel_vs "${CMAKE_CURRENT_BINARY_DIR}/pixel_aot_vs.h")
add_custom_command(OUTPUT "${pixel_vs}" COMMAND "${DXC_EXECUTABLE}" -T vs_6_0 -E main
    -Fh "${pixel_vs}" -Vn pixel_aot_vs "${CMAKE_CURRENT_SOURCE_DIR}/tests/pixel_aot_vs.hlsl"
    DEPENDS tests/pixel_aot_vs.hlsl VERBATIM)
target_sources(offscreen_check PRIVATE tests/pixel_aot_render.cpp "${pixel_vs}")
target_sources(offscreen_check PRIVATE tests/binding_mips.cpp tests/ffx_recurrent_views.cpp)
target_sources(offscreen_check PRIVATE tests/ffx_rcas_views.cpp)
foreach(storage IN ITEMS Native Packed)
    foreach(op IN ITEMS Read Write)
        set(symbol "Rcas${op}${storage}")
        set(header "${CMAKE_CURRENT_BINARY_DIR}/${symbol}.h")
        add_custom_command(OUTPUT "${header}"
            COMMAND "${DXC_EXECUTABLE}" -T cs_6_0 -E "Rcas${op}" -D "STORAGE_${storage}=1"
                -Vn "${symbol}" -Fh "${header}" -Fo "${CMAKE_CURRENT_BINARY_DIR}/${symbol}.cso"
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/ffx_rcas.hlsl"
            DEPENDS tests/ffx_rcas.hlsl src/rgb9e5.hlsli BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${symbol}.cso" VERBATIM)
        target_sources(offscreen_check PRIVATE "${header}")
    endforeach()
endforeach()
foreach(entry IN ITEMS DualUav SampleMip WriteRecurrent)
    set(header "${CMAKE_CURRENT_BINARY_DIR}/${entry}.h")
    add_custom_command(OUTPUT "${header}" COMMAND "${DXC_EXECUTABLE}" -T cs_6_0 -E ${entry}
        -Vn ${entry} -Fh "${header}" -Fo "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/binding_mips.hlsl" DEPENDS tests/binding_mips.hlsl
        BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso" VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
endforeach()
target_sources(offscreen_check PRIVATE tests/bvh_reads_waves.cpp tests/bvh_hits.cpp)
target_sources(offscreen_check PRIVATE tests/bvh_atomics.cpp tests/bvh_numeric.cpp)
foreach(entry IN ITEMS InitBounds AtomicBounds QueryBounds NumericBounds)
    set(bounds_source tests/bvh_atomics.hlsl)
    if(entry STREQUAL "NumericBounds")
        set(bounds_source tests/bvh_numeric.hlsl)
    endif()
    set(header "${CMAKE_CURRENT_BINARY_DIR}/${entry}.h")
    add_custom_command(OUTPUT "${header}" COMMAND "${DXC_EXECUTABLE}" -T cs_6_5 -E ${entry}
        -Vn ${entry} -Fh "${header}" -Fo "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso"
        "${CMAKE_CURRENT_SOURCE_DIR}/${bounds_source}" DEPENDS "${bounds_source}"
        BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso" VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
endforeach()
foreach(entry IN ITEMS Produce ReadValues WaveValues Wave64 WriteScene ExpandLeaves TraceHits)
    set(header "${CMAKE_CURRENT_BINARY_DIR}/${entry}.h")
    set(wave_args -T cs_6_5 -E ${entry})
    if(entry STREQUAL "Wave64")
        set(wave_args -T cs_6_6 -E WaveValues -D REQUIRE_WAVE64=1)
    endif()
    add_custom_command(OUTPUT "${header}" COMMAND "${DXC_EXECUTABLE}" ${wave_args} -Vn ${entry}
        -Fh "${header}" -Fo "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/bvh_reads_waves.hlsl" DEPENDS tests/bvh_reads_waves.hlsl
        BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso" VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
endforeach()
target_sources(offscreen_check PRIVATE tests/wave_shuffle.cpp)
foreach(width IN ITEMS 0 16 32 64)
    if(width)
        set(entry WaveShuffle${width})
        set(shuffle_args -T cs_6_6 -D WAVE_SIZE=${width})
    else()
        set(entry WaveShuffle)
        set(shuffle_args -T cs_6_5)
    endif()
    set(header "${CMAKE_CURRENT_BINARY_DIR}/${entry}.h")
    add_custom_command(OUTPUT "${header}" COMMAND "${DXC_EXECUTABLE}" ${shuffle_args} -E Shuffle -Vn ${entry}
        -Fh "${header}" -Fo "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/wave_shuffle.hlsl" DEPENDS tests/wave_shuffle.hlsl
        BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso" VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
endforeach()
foreach(entry IN ITEMS CopyBlocks FillBlocks NumberBlocks CopySpan ResetBounds)
    set(header "${CMAKE_CURRENT_BINARY_DIR}/${entry}.h")
    add_custom_command(OUTPUT "${header}" COMMAND "${DXC_EXECUTABLE}" -T cs_6_5 -E ${entry} -Vn ${entry}
        -Fh "${header}" -Fo "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso"
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/buffer_ops.hlsl" DEPENDS tests/buffer_ops.hlsl
        BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${entry}.cso" VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
endforeach()
add_custom_command(OUTPUT "${ray_header}"
    COMMAND "${DXC_EXECUTABLE}" -T cs_6_5 -E CSMain -Vn ray_query -Fh "${ray_header}"
            -Fo "${CMAKE_CURRENT_BINARY_DIR}/ray_query.cso" "${CMAKE_CURRENT_SOURCE_DIR}/tests/ray_query.hlsl"
    BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/ray_query.cso" DEPENDS tests/ray_query.hlsl VERBATIM)
target_sources(offscreen_check PRIVATE "${ray_header}" tests/dxr_facing.cpp)
foreach(layout IN ITEMS root tables)
    set(header "${CMAKE_CURRENT_BINARY_DIR}/compute_${layout}.h")
    set(binary "${CMAKE_CURRENT_BINARY_DIR}/compute_${layout}.cso")
    set(defines)
    if(layout STREQUAL "tables")
        set(defines -D TABLE_BINDINGS=1)
    endif()
    add_custom_command(OUTPUT "${header}" "${binary}"
        COMMAND "${DXC_EXECUTABLE}" -T cs_6_0 -E CSMain ${defines} -Vn compute_${layout}
                -Fh "${header}" -Fo "${binary}" "${CMAKE_CURRENT_SOURCE_DIR}/tests/compute.hlsl"
        DEPENDS tests/compute.hlsl VERBATIM)
    target_sources(offscreen_check PRIVATE "${header}")
    foreach(adapter IN ITEMS hardware warp)
        add_test(NAME compute_${layout}_${adapter} COMMAND offscreen_check ${adapter} --compute-${layout})
        set_tests_properties(compute_${layout}_${adapter} PROPERTIES TIMEOUT 45)
    endforeach()
    set_tests_properties(compute_${layout}_hardware PROPERTIES SKIP_RETURN_CODE 77)
endforeach()
foreach(adapter IN ITEMS hardware warp)
    add_test(NAME offscreen_${adapter} COMMAND offscreen_check ${adapter} triangle_${adapter}.bmp)
    add_test(NAME ffx_provider_loop_${adapter} COMMAND offscreen_check ${adapter} --ffx-provider-loop)
    add_test(NAME ffx_device_init_${adapter} COMMAND offscreen_check ${adapter} --ffx-device-init)
    add_test(NAME ffx_recurrent_${adapter} COMMAND offscreen_check ${adapter} --ffx-recurrent)
    add_test(NAME dual_uav_${adapter} COMMAND offscreen_check ${adapter} --dual-uav)
    add_test(NAME texture_mips_${adapter} COMMAND offscreen_check ${adapter} --texture-mips)
    set_tests_properties(dual_uav_${adapter} texture_mips_${adapter} PROPERTIES TIMEOUT 45)
    add_test(NAME dxr_query_${adapter} COMMAND offscreen_check ${adapter} --ray-query)
    add_test(NAME dxr_update_${adapter} COMMAND offscreen_check ${adapter} --dxr-update)
    set_tests_properties(dxr_update_${adapter} PROPERTIES TIMEOUT 45 SKIP_RETURN_CODE 77)
    add_test(NAME dxr_facing_${adapter} COMMAND offscreen_check ${adapter} --dxr-facing)
    set_tests_properties(dxr_facing_${adapter} PROPERTIES TIMEOUT 45 SKIP_RETURN_CODE 77)
    add_test(NAME bvh_buffer_ops_${adapter} COMMAND offscreen_check ${adapter} --buffer-ops)
    add_test(NAME bvh_root_roundtrip_${adapter} COMMAND offscreen_check ${adapter} --bvh-root-roundtrip)
    set_tests_properties(bvh_root_roundtrip_${adapter} PROPERTIES TIMEOUT 45)
    foreach(work IN ITEMS reads waves wave64 hits differential atomics numeric packed)
        add_test(NAME bvh_${work}_${adapter} COMMAND offscreen_check ${adapter} --bvh-${work})
        set_tests_properties(bvh_${work}_${adapter} PROPERTIES TIMEOUT 45)
    endforeach()
    set_tests_properties(bvh_wave64_${adapter} bvh_differential_${adapter} PROPERTIES SKIP_RETURN_CODE 77)
    add_test(NAME wave_shuffle_${adapter} COMMAND offscreen_check ${adapter} --wave-shuffle)
    set_tests_properties(wave_shuffle_${adapter} PROPERTIES TIMEOUT 45 SKIP_RETURN_CODE 77)
    set_tests_properties(bvh_buffer_ops_${adapter} PROPERTIES TIMEOUT 45)
    set_tests_properties(dxr_query_${adapter} PROPERTIES TIMEOUT 45 SKIP_RETURN_CODE 77)
    set_tests_properties(offscreen_${adapter} PROPERTIES TIMEOUT 45)
endforeach()
set_tests_properties(offscreen_hardware ffx_device_init_hardware ffx_recurrent_hardware ffx_provider_loop_hardware PROPERTIES SKIP_RETURN_CODE 77)
set_tests_properties(dual_uav_hardware texture_mips_hardware PROPERTIES SKIP_RETURN_CODE 77)
set_tests_properties(bvh_root_roundtrip_hardware PROPERTIES SKIP_RETURN_CODE 77)
set_tests_properties(bvh_atomics_hardware PROPERTIES SKIP_RETURN_CODE 77)
set_tests_properties(bvh_numeric_hardware bvh_packed_hardware PROPERTIES SKIP_RETURN_CODE 77)
set_tests_properties(bvh_buffer_ops_hardware PROPERTIES SKIP_RETURN_CODE 77)
set_tests_properties(bvh_reads_hardware bvh_waves_hardware bvh_hits_hardware PROPERTIES SKIP_RETURN_CODE 77)
# The cs_6_5 fixtures skip on a WARP below SM 6.5. The Windows 10 in-box WARP reports SM 6.2.
set_tests_properties(bvh_buffer_ops_warp bvh_reads_warp bvh_waves_warp bvh_hits_warp bvh_packed_warp
    bvh_numeric_warp bvh_atomics_warp PROPERTIES SKIP_RETURN_CODE 77)

set(SIMPLE_TRIANGLE_DIR "${CMAKE_SOURCE_DIR}/out/reference/b1e77b6ac10f95b07296025fda171ca2ddc42dbe/Samples/IntroGraphics/SimpleTriangle"
    CACHE PATH "Microsoft SimpleTriangle sample directory")
if(EXISTS "${SIMPLE_TRIANGLE_DIR}/VertexShader.hlsl")
    find_program(REFERENCE_POWERSHELL NAMES pwsh powershell REQUIRED)
    foreach(adapter IN ITEMS hardware warp)
        add_test(NAME reference_triangle_${adapter} COMMAND "${REFERENCE_POWERSHELL}" -NoProfile -ExecutionPolicy Bypass
            -File "${CMAKE_SOURCE_DIR}/tests/reference_triangle.ps1" -Sample "${SIMPLE_TRIANGLE_DIR}"
            -Compiler "${DXC_EXECUTABLE}" -Renderer "$<TARGET_FILE:offscreen_check>" -Adapter ${adapter})
        set_tests_properties(reference_triangle_${adapter} PROPERTIES TIMEOUT 60 SKIP_RETURN_CODE 77)
    endforeach()
endif()

set(INTAKE_PYTHON "${CMAKE_SOURCE_DIR}/out/pe-env/Scripts/python.exe" CACHE FILEPATH
    "Native x64 Python with pefile for static intake tests")
target_sources(offscreen_check PRIVATE tests/ffx_device_init.cpp tests/ffx_backend_slots.cpp tests/ffx_recurrent.cpp tests/ffx_provider_loop.cpp)
set(FFX_UPSCALER_IMAGE "" CACHE FILEPATH "Local Xbox upscaler DLL for offline backend translation")
set(FFX_DISPATCH_SHADER_DIR "" CACHE PATH "Directory containing locally translated FSR4 shaders and scratch_reference.h")
if(FFX_DISPATCH_SHADER_DIR)
    if(NOT FFX_UPSCALER_IMAGE)
        message(FATAL_ERROR "Package dispatch replay requires FFX_UPSCALER_IMAGE")
    endif()
    target_compile_definitions(offscreen_check PRIVATE FFX_DISPATCH_AOT)
    target_include_directories(offscreen_check PRIVATE "${FFX_DISPATCH_SHADER_DIR}")
endif()
target_sources(offscreen_check PRIVATE tests/ffx_dispatch.cpp tests/ffx_dispatch_scratch.cpp)
foreach(stage IN ITEMS pass24 pass25 producer rcas)
    set(shader "${CMAKE_CURRENT_SOURCE_DIR}/tests/ffx_dispatch_control.hlsl")
    set(entry "${stage}")
    if(FFX_DISPATCH_SHADER_DIR)
        set(shader "${FFX_DISPATCH_SHADER_DIR}/${stage}.hlsl")
        set(entry main)
    endif()
    set(storage_formats Native Packed)
    if(stage MATCHES "^pass")
        set(storage_formats Raw)
    endif()
    foreach(storage IN LISTS storage_formats)
        set(symbol "${stage}${storage}")
        set(header "${CMAKE_CURRENT_BINARY_DIR}/${symbol}.h")
        add_custom_command(OUTPUT "${header}"
            COMMAND "${DXC_EXECUTABLE}" -T cs_6_0 -E "${entry}" -D "STORAGE_${storage}=1"
                -I "${CMAKE_CURRENT_SOURCE_DIR}/tests" -Vn "${symbol}" -Fh "${header}"
                -Fo "${CMAKE_CURRENT_BINARY_DIR}/${symbol}.cso" "${shader}"
            DEPENDS "${shader}" tests/ffx_dispatch_io.hlsli src/rgb9e5.hlsli
            BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/${symbol}.cso" VERBATIM)
        target_sources(offscreen_check PRIVATE "${header}")
    endforeach()
endforeach()
if(FFX_UPSCALER_IMAGE)
    if(NOT EXISTS "${INTAKE_PYTHON}" OR NOT EXISTS "${FFX_UPSCALER_IMAGE}")
        message(FATAL_ERROR "Package translation requires INTAKE_PYTHON and FFX_UPSCALER_IMAGE")
    endif()
    set(device_slice "${CMAKE_CURRENT_BINARY_DIR}/ffx_device_slice.cpp")
    add_custom_command(OUTPUT "${device_slice}"
        COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/ffx_device_init.py" "${FFX_UPSCALER_IMAGE}" "${device_slice}"
        DEPENDS "${FFX_UPSCALER_IMAGE}" tools/ffx_device_init.py tools/ffx_recurrent.py tools/ffx_provider_loop.py tools/ffx_dispatch_profile.py tools/ffx_version_query.py tools/cpu_ranges.py tools/cpu_leaf_aot.py
        VERBATIM)
    target_sources(offscreen_check PRIVATE "${device_slice}")
    target_include_directories(offscreen_check PRIVATE "${CMAKE_SOURCE_DIR}/tests")
    target_compile_definitions(offscreen_check PRIVATE FFX_PACKAGE_DEVICE_SLICE)
    get_filename_component(ffx_dir "${FFX_UPSCALER_IMAGE}" DIRECTORY)
    set(ffx_loader "${ffx_dir}/amd_fidelityfx_loader_xbox.dll")
    if(EXISTS "${ffx_loader}")
        set(loop_loader "${CMAKE_CURRENT_BINARY_DIR}/ffx_loop_loader.cpp")
        add_custom_command(OUTPUT "${loop_loader}"
            COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/ffx_create_context.py" "${ffx_loader}" "${loop_loader}"
            DEPENDS "${ffx_loader}" tools/ffx_create_context.py tools/ffx_version_query.py tools/cpu_leaf_aot.py tools/cpu_ranges.py
            VERBATIM)
        set_source_files_properties("${loop_loader}" PROPERTIES HEADER_FILE_ONLY TRUE)
        target_sources(offscreen_check PRIVATE "${loop_loader}" tests/ffx_provider_loader.cpp)
        target_compile_definitions(offscreen_check PRIVATE FFX_LOOP_LOADER)
    endif()
endif()
if(EXISTS "${INTAKE_PYTHON}")
    enable_language(ASM_MASM)
    function(cpu_asm_fixture image source)
        if(ARGC GREATER 2)
            set(exports "${ARGV2}")
        else()
            set(exports "${source}")
        endif()
        add_library(${image} SHARED tests/cpu/${source}.asm tests/cpu/${exports}.def)
        set_target_properties(${image} PROPERTIES LINKER_LANGUAGE CXX)
        target_link_options(${image} PRIVATE /NOENTRY /NODEFAULTLIB /INCREMENTAL:NO)
    endfunction()
    function(cpu_translation image translator entry)
        cmake_parse_arguments(BIND "FFX_DESTROY" "PROVIDER;SYMBOL;LINK_NAME;CALLBACK_OFFSET" "" ${ARGN})
        set(binding)
        if(BIND_FFX_DESTROY)
            list(APPEND binding --ffx-destroy)
        endif()
        if(DEFINED BIND_CALLBACK_OFFSET)
            list(APPEND binding --callback-offset "${BIND_CALLBACK_OFFSET}")
        endif()
        if(BIND_PROVIDER)
            set(binding --bind "$<TARGET_FILE:${BIND_PROVIDER}>" "${BIND_SYMBOL}")
            if(BIND_LINK_NAME)
                list(APPEND binding --link-name "${BIND_LINK_NAME}")
            endif()
        endif()
        set(lifted "${CMAKE_CURRENT_BINARY_DIR}/${image}_lifted.cpp")
        add_custom_command(OUTPUT "${lifted}"
            COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/${translator}.py"
                "$<TARGET_FILE:${image}>" ${entry} "${lifted}" ${binding}
            DEPENDS ${image} ${BIND_PROVIDER} tools/${translator}.py tools/cpu_leaf_aot.py tools/cpu_ranges.py VERBATIM)
        add_library(${image}_lifted SHARED "${lifted}")
        if(BIND_PROVIDER)
            target_link_libraries(${image}_lifted PRIVATE ${BIND_PROVIDER})
        endif()
    endfunction()
    foreach(variant IN ITEMS original inverted)
        cpu_asm_fixture(cpu_leaf_${variant} branch)
        cpu_translation(cpu_leaf_${variant} cpu_leaf_aot branch_value)
    endforeach()
    target_compile_definitions(cpu_leaf_inverted PRIVATE INVERT_BRANCH)
    add_test(NAME cpu_leaf_aot_branches COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/branches.py" "$<TARGET_FILE:cpu_leaf_original>"
        "$<TARGET_FILE:cpu_leaf_original_lifted>" "$<TARGET_FILE:cpu_leaf_inverted_lifted>")
    foreach(variant IN ITEMS original altered)
        cpu_asm_fixture(cpu_call_${variant} direct)
        cpu_translation(cpu_call_${variant} cpu_call_aot call_mix)
        # Keep the linked caller's rel32 directed at the callee itself for inspection.
        target_link_options(cpu_call_${variant}_lifted PRIVATE /INCREMENTAL:NO)
    endforeach()
    target_compile_definitions(cpu_call_altered PRIVATE ALTER_RETURN)
    cpu_asm_fixture(cpu_register_probe register_probe)
    foreach(variant IN ITEMS original swapped)
        cpu_asm_fixture(cpu_switch_${variant} switch)
        cpu_translation(cpu_switch_${variant} cpu_switch_aot select_rva,select_rel,run_ops,shift_narrow)
    endforeach()
    target_compile_definitions(cpu_switch_swapped PRIVATE SWAP_TABLE)
    add_test(NAME cpu_aot_jump_tables COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/switch_check.py" "$<TARGET_FILE:cpu_register_probe>"
        "$<TARGET_FILE:cpu_switch_original>" "$<TARGET_FILE:cpu_switch_original_lifted>"
        "$<TARGET_FILE:cpu_switch_swapped_lifted>")
    cpu_asm_fixture(cpu_chained chained)
    cpu_translation(cpu_chained cpu_chained_aot choose_value)
    cpu_asm_fixture(cpu_callback callback)
    cpu_asm_fixture(cpu_version_query version_query)
    cpu_asm_fixture(cpu_heap_layout heap_layout)
    cpu_asm_fixture(cpu_bink_heap bink_heap)
    add_library(cpu_bink_heap_provider SHARED tests/cpu/bink_heap_provider.cpp)
    target_link_libraries(cpu_bink_heap PRIVATE cpu_bink_heap_provider)
    function(bink_heap_translation target image dependency)
        set(cpp "${CMAKE_CURRENT_BINARY_DIR}/${target}.cpp")
        add_custom_command(OUTPUT "${cpp}"
            COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/bink_heap_aot.py"
                "${image}" "${cpp}" ${ARGN}
            DEPENDS ${dependency} tools/bink_heap_aot.py tools/ffx_version_query.py
                tools/cpu_leaf_aot.py tools/cpu_ranges.py VERBATIM)
        add_library(${target} SHARED "${cpp}")
        target_link_libraries(${target} PRIVATE cpu_bink_heap_provider)
    endfunction()
    bink_heap_translation(cpu_bink_heap_lifted "$<TARGET_FILE:cpu_bink_heap>" cpu_bink_heap --group-entry heap_group)
    add_test(NAME cpu_aot_bink_heap COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/bink_heap_check.py" "$<TARGET_FILE:cpu_bink_heap_provider>"
        "$<TARGET_FILE:cpu_bink_heap>" "$<TARGET_FILE:cpu_bink_heap_lifted>")
    add_test(NAME cpu_aot_bink_group COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/bink_group_check.py" "$<TARGET_FILE:cpu_bink_heap_provider>"
        "$<TARGET_FILE:cpu_bink_heap>" "$<TARGET_FILE:cpu_bink_heap_lifted>")
    set(BINK_GROUP_ENTRY "" CACHE STRING "Optional grouped allocation caller RVA in the local Bink image")
    set(BINK_HEAP_IMAGE "" CACHE FILEPATH "Local PE containing the Bink allocation and release callers")
    set(BINK_HEAP_ENTRIES "" CACHE STRING "Two semicolon-separated entry RVAs (allocation;release)")
    if(BINK_HEAP_IMAGE)
        list(LENGTH BINK_HEAP_ENTRIES entry_count)
        if(NOT entry_count EQUAL 2)
            message(FATAL_ERROR "Provide both BINK_HEAP_ENTRIES for the selected local image")
        endif()
        set(group_args)
        if(BINK_GROUP_ENTRY)
            set(group_args --group-entry "${BINK_GROUP_ENTRY}")
            add_test(NAME cpu_aot_bink_package_group COMMAND "${INTAKE_PYTHON}" -B
                "${CMAKE_SOURCE_DIR}/tests/cpu/bink_group_check.py" "$<TARGET_FILE:cpu_bink_heap_provider>"
                "$<TARGET_FILE:cpu_bink_package_lifted>")
        endif()
        bink_heap_translation(cpu_bink_package_lifted "${BINK_HEAP_IMAGE}" "${BINK_HEAP_IMAGE}"
            --entries ${BINK_HEAP_ENTRIES} ${group_args})
        add_test(NAME cpu_aot_bink_package_heap COMMAND "${INTAKE_PYTHON}" -B
            "${CMAKE_SOURCE_DIR}/tests/cpu/bink_heap_check.py" "$<TARGET_FILE:cpu_bink_heap_provider>"
            "$<TARGET_FILE:cpu_bink_package_lifted>")
    endif()
    cpu_asm_fixture(cpu_bink_descriptors bink_descriptors)
    function(bink_descriptors_translation target image dependency)
        set(cpp "${CMAKE_CURRENT_BINARY_DIR}/${target}.cpp")
        add_custom_command(OUTPUT "${cpp}"
            COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/bink_descriptors_aot.py"
                "${image}" "${cpp}" ${ARGN}
            DEPENDS ${dependency} tools/bink_descriptors_aot.py tools/bink_heap_aot.py
                tools/ffx_version_query.py tools/cpu_ranges.py tools/cpu_leaf_aot.py VERBATIM)
        add_library(${target} SHARED "${cpp}")
    endfunction()
    bink_descriptors_translation(cpu_bink_descriptors_lifted "$<TARGET_FILE:cpu_bink_descriptors>" cpu_bink_descriptors)
    add_test(NAME cpu_bink_descriptors COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/bink_descriptors_check.py" --native "$<TARGET_FILE:cpu_bink_descriptors>"
        "$<TARGET_FILE:cpu_bink_descriptors_lifted>")
    set(BINK_DESCRIPTOR_IMAGE "" CACHE FILEPATH "Local Bink DLL for the grouped descriptor producer experiment")
    if(BINK_DESCRIPTOR_IMAGE)
        bink_descriptors_translation(cpu_bink_package_descriptors "${BINK_DESCRIPTOR_IMAGE}" "${BINK_DESCRIPTOR_IMAGE}" --package)
        add_test(NAME cpu_bink_package_descriptors COMMAND "${INTAKE_PYTHON}" -B
            "${CMAKE_SOURCE_DIR}/tests/cpu/bink_descriptors_check.py" "$<TARGET_FILE:cpu_bink_package_descriptors>")
    endif()
    set(heap_fixture "${CMAKE_CURRENT_BINARY_DIR}/cpu_heap_layout_lifted.cpp")
    add_custom_command(OUTPUT "${heap_fixture}"
        COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tests/cpu/heap_layout.py"
            emit "$<TARGET_FILE:cpu_heap_layout>" "${heap_fixture}"
        DEPENDS cpu_heap_layout tests/cpu/heap_layout.py tools/ffx_version_query.py
            tools/cpu_ranges.py tools/cpu_leaf_aot.py VERBATIM)
    add_library(cpu_heap_layout_lifted SHARED "${heap_fixture}")
    add_test(NAME cpu_aot_heap_layout COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tests/cpu/heap_layout.py"
        check "$<TARGET_FILE:cpu_heap_layout>" "$<TARGET_FILE:cpu_heap_layout_lifted>")
    set(query_fixture "${CMAKE_CURRENT_BINARY_DIR}/cpu_version_query_lifted.cpp")
    add_custom_command(OUTPUT "${query_fixture}"
        COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tests/cpu/version_query_fixture.py"
            emit "$<TARGET_FILE:cpu_version_query>" "${query_fixture}"
        DEPENDS cpu_version_query tests/cpu/version_query_fixture.py tools/ffx_version_query.py
            tools/cpu_ranges.py tools/cpu_leaf_aot.py tests/cpu/version_query_check.py VERBATIM)
    add_library(cpu_version_query_lifted SHARED "${query_fixture}")
    add_test(NAME cpu_aot_version_query COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/version_query_fixture.py" check "$<TARGET_FILE:cpu_version_query>"
        "$<TARGET_FILE:cpu_version_query_lifted>")
    cpu_translation(cpu_callback cpu_chained_aot call_context CALLBACK_OFFSET 8)
    cpu_asm_fixture(cpu_destroy destroy)
    target_link_libraries(cpu_destroy PRIVATE ucrt.lib)
    cpu_translation(cpu_destroy cpu_chained_aot release_context FFX_DESTROY)
    add_library(cpu_destroy_provider SHARED tests/cpu/destroy_provider.cpp)
    cpu_asm_fixture(cpu_create create_context)
    set(create_fixture "${CMAKE_CURRENT_BINARY_DIR}/cpu_create_lifted.cpp")
    add_custom_command(OUTPUT "${create_fixture}"
        COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tests/cpu/create_fixture.py"
            "$<TARGET_FILE:cpu_create>" "${create_fixture}"
        DEPENDS cpu_create tests/cpu/create_fixture.py tools/ffx_create_context.py
            tools/ffx_version_query.py tools/cpu_leaf_aot.py tools/cpu_ranges.py VERBATIM)
    add_library(cpu_create_lifted SHARED "${create_fixture}")
    add_test(NAME cpu_aot_context_lifecycle COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/context_lifecycle.py" "$<TARGET_FILE:cpu_create>"
        "$<TARGET_FILE:cpu_create_lifted>" "$<TARGET_FILE:cpu_destroy_lifted>")
    add_test(NAME cpu_aot_destroy_contract COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/destroy_check.py" "$<TARGET_FILE:cpu_destroy>"
        "$<TARGET_FILE:cpu_destroy_lifted>" "$<TARGET_FILE:cpu_destroy_provider>")
    add_test(NAME cpu_aot_object_callback COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/callback_check.py" "$<TARGET_FILE:cpu_callback>"
        "$<TARGET_FILE:cpu_callback_lifted>")
    add_test(NAME cpu_aot_chained_ranges COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/chained_check.py" "$<TARGET_FILE:cpu_chained>"
        "$<TARGET_FILE:cpu_chained_lifted>" "$<TARGET_FILE:cpu_register_probe>")
    add_test(NAME cpu_aot_direct_call COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/direct_check.py" "$<TARGET_FILE:cpu_register_probe>"
        "$<TARGET_FILE:cpu_call_original>" "$<TARGET_FILE:cpu_call_original_lifted>"
        "$<TARGET_FILE:cpu_call_altered_lifted>")
    foreach(kind IN ITEMS named ordinal)
        if(kind STREQUAL "named")
            set(prefix cpu_import)
            set(contract SYMBOL mix_args)
        else()
            set(prefix cpu_ordinal)
            set(contract SYMBOL "#77" LINK_NAME mix_args)
        endif()
        foreach(variant IN ITEMS provider altered)
            add_library(${prefix}_${variant} SHARED tests/cpu/import_provider.cpp tests/cpu/${kind}_provider.def)
        endforeach()
        target_compile_definitions(${prefix}_altered PRIVATE ALTER_RETURN)
        cpu_asm_fixture(${prefix}_original direct import)
        target_compile_definitions(${prefix}_original PRIVATE IMPORT_MIX)
        target_link_libraries(${prefix}_original PRIVATE ${prefix}_provider)
        cpu_translation(${prefix}_original cpu_call_aot call_mix PROVIDER ${prefix}_provider ${contract})
        target_link_options(${prefix}_original_lifted PRIVATE /INCREMENTAL:NO)
    endforeach()
    add_test(NAME cpu_aot_import_call COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/import_check.py" "$<TARGET_FILE:cpu_import_original>"
        "$<TARGET_FILE:cpu_import_original_lifted>" "$<TARGET_FILE:cpu_import_provider>"
        "$<TARGET_FILE:cpu_import_altered>" "$<TARGET_FILE:cpu_register_probe>")
    add_test(NAME cpu_aot_ordinal_import COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/import_check.py" "$<TARGET_FILE:cpu_ordinal_original>"
        "$<TARGET_FILE:cpu_ordinal_original_lifted>" "$<TARGET_FILE:cpu_ordinal_provider>"
        "$<TARGET_FILE:cpu_ordinal_altered>" "$<TARGET_FILE:cpu_register_probe>"
        --ordinal-control "$<TARGET_FILE:cpu_import_provider>")
    cpu_asm_fixture(cpu_reloc_original reloc_route)
    target_link_libraries(cpu_reloc_original PRIVATE cpu_import_provider)
    cpu_asm_fixture(cpu_two_imports two_imports)
    target_link_libraries(cpu_two_imports PRIVATE cpu_import_provider cpu_register_probe)
    add_library(pe_map SHARED tools/pe_map.cpp)
    target_compile_features(pe_map PRIVATE cxx_std_17)
    add_test(NAME cpu_native_map COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_map_check.py" "$<TARGET_FILE:pe_map>"
        "$<TARGET_FILE:cpu_register_probe>"
        --named "$<TARGET_FILE:cpu_import_original>" "$<TARGET_FILE:cpu_import_original_lifted>"
            "$<TARGET_FILE:cpu_import_provider>" "$<TARGET_FILE:cpu_import_altered>"
        --ordinal "$<TARGET_FILE:cpu_ordinal_original>" "$<TARGET_FILE:cpu_ordinal_original_lifted>"
            "$<TARGET_FILE:cpu_ordinal_provider>" "$<TARGET_FILE:cpu_ordinal_altered>"
        --reloc "$<TARGET_FILE:cpu_reloc_original>" "$<TARGET_FILE:cpu_import_provider>"
            "$<TARGET_FILE:cpu_import_altered>"
        --two "$<TARGET_FILE:cpu_two_imports>" "$<TARGET_FILE:cpu_import_provider>"
            "$<TARGET_FILE:cpu_register_probe>")
    add_test(NAME cpu_native_reloc_free COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_reloc_free_check.py" "$<TARGET_FILE:pe_map>"
        "$<TARGET_FILE:cpu_register_probe>" "$<TARGET_FILE:cpu_import_original>"
        "$<TARGET_FILE:cpu_import_provider>")
    cpu_asm_fixture(cpu_seh_original seh_route)
    add_test(NAME cpu_native_seh COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_seh_check.py" "$<TARGET_FILE:cpu_seh_original>"
        "$<TARGET_FILE:pe_map>" "$<TARGET_FILE:cpu_register_probe>")
    add_test(NAME cpu_native_section COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_section_check.py" "$<TARGET_FILE:pe_map>"
        "$<TARGET_FILE:cpu_register_probe>")
    add_library(cpu_tls_sink SHARED tests/cpu/tls_sink.cpp)
    foreach(variant IN ITEMS original refuse)
        add_library(cpu_tls_${variant} SHARED tests/cpu/tls_route.asm tests/cpu/tls_route.def)
        set_target_properties(cpu_tls_${variant} PROPERTIES LINKER_LANGUAGE CXX)
        target_link_options(cpu_tls_${variant} PRIVATE /ENTRY:dll_entry /NODEFAULTLIB /INCREMENTAL:NO)
        target_link_libraries(cpu_tls_${variant} PRIVATE cpu_tls_sink)
    endforeach()
    target_compile_definitions(cpu_tls_refuse PRIVATE ENTRY_FAILS)
    add_test(NAME cpu_native_tls COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_tls_check.py" "$<TARGET_FILE:cpu_tls_original>"
        "$<TARGET_FILE:cpu_tls_refuse>" "$<TARGET_FILE:cpu_tls_sink>" "$<TARGET_FILE:pe_map>")
    add_library(cpu_frame_host SHARED tests/cpu/frame_host.cpp)
    add_library(cpu_frame_guest SHARED tests/cpu/tls_route.asm tests/cpu/frame_route.def)
    set_target_properties(cpu_frame_guest PROPERTIES LINKER_LANGUAGE CXX)
    target_compile_definitions(cpu_frame_guest PRIVATE FRAME_GUEST)
    target_link_options(cpu_frame_guest PRIVATE /ENTRY:dll_entry /NODEFAULTLIB /INCREMENTAL:NO)
    target_link_libraries(cpu_frame_guest PRIVATE cpu_tls_sink cpu_frame_host)

    add_library(cpu_delay_provider SHARED tests/cpu/delay_provider.cpp tests/cpu/delay_provider.def)
    add_library(cpu_delay_provider_altered SHARED tests/cpu/delay_provider.cpp
        tests/cpu/delay_provider_altered.def)
    target_compile_definitions(cpu_delay_provider_altered PRIVATE ALTER_RETURN)
    add_library(cpu_delay_route SHARED tests/cpu/delay_route.cpp)
    target_link_libraries(cpu_delay_route PRIVATE cpu_delay_provider delayimp)
    target_link_options(cpu_delay_route PRIVATE /DELAYLOAD:cpu_delay_provider.dll /INCREMENTAL:NO)
    add_test(NAME cpu_native_delay COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_delay_check.py" "$<TARGET_FILE:pe_map>"
        "$<TARGET_FILE:cpu_delay_route>" "$<TARGET_FILE:cpu_delay_provider>"
        "$<TARGET_FILE:cpu_delay_provider_altered>")
    add_test(NAME cpu_native_lifetime COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_lifetime_check.py" "$<TARGET_FILE:pe_map>"
        "$<TARGET_FILE:cpu_tls_original>" "$<TARGET_FILE:cpu_tls_sink>")
    # 3000 long export names for one routine, so the name strings and the tables span several pages.
    set(many_exports_def "${CMAKE_CURRENT_BINARY_DIR}/many_exports.def")
    set(many_exports_text "EXPORTS\n")
    foreach(index RANGE 1 3000)
        string(APPEND many_exports_text "    export_name_long_enough_to_spread_strings_over_pages_${index}=named_target\n")
    endforeach()
    file(WRITE "${many_exports_def}" "${many_exports_text}")
    add_library(cpu_many_exports SHARED tests/cpu/many_exports.asm "${many_exports_def}")
    set_target_properties(cpu_many_exports PROPERTIES LINKER_LANGUAGE CXX)
    target_link_options(cpu_many_exports PRIVATE /NOENTRY /NODEFAULTLIB /INCREMENTAL:NO)
    add_test(NAME cpu_native_map_hardening COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/cpu/native_map_hardening_check.py" "$<TARGET_FILE:pe_map>"
        "$<TARGET_FILE:cpu_register_probe>" "$<TARGET_FILE:cpu_tls_original>" "$<TARGET_FILE:cpu_tls_sink>"
        "$<TARGET_FILE:cpu_many_exports>")
    add_library(script_path_host SHARED tools/script_path_host.cpp)
    target_compile_features(script_path_host PRIVATE cxx_std_17)
    add_test(NAME script_path_links COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/script_path_check.py" "$<TARGET_FILE:script_path_host>")
    add_executable(ffx_pass26_lds tests/ffx_pass26_lds/gate.cpp)
    target_link_libraries(ffx_pass26_lds PRIVATE d3d11 d3dcompiler)
    add_test(NAME ffx_pass26_lds_warp COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/ffx_pass26_lds/verify.py"
        --host $<TARGET_FILE:ffx_pass26_lds> --backend warp)

    add_test(NAME script_state_literals COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/script_state_check.py")
    add_test(NAME script_dispatch_pairs COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/script_dispatch_check.py" "$<TARGET_FILE:script_path_host>")
    add_test(NAME bcpack_prefix_and_mip_tail COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/bcpack_check.py")
    add_test(NAME bc7_block_reference COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/bc7_blocks_check.py")
    add_test(NAME resource_range_controls COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/resource_ranges_check.py")
    add_test(NAME rdna2_tools_check COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/rdna2_tools_check.py")
    add_test(NAME dxcontainer_part_offsets COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/dxcontainer_part_offsets_check.py")
    foreach(adapter IN ITEMS hardware warp)
        add_test(NAME cpu_native_frame_${adapter} COMMAND "${INTAKE_PYTHON}" -B
            "${CMAKE_SOURCE_DIR}/tests/cpu/native_frame_check.py" "$<TARGET_FILE:cpu_frame_guest>"
            "$<TARGET_FILE:cpu_tls_sink>" "$<TARGET_FILE:cpu_frame_host>" "$<TARGET_FILE:pe_map>"
            "${DXC_EXECUTABLE}" "$<TARGET_FILE:offscreen_check>" ${adapter})
        set_tests_properties(cpu_native_frame_${adapter} PROPERTIES TIMEOUT 95)
        add_test(NAME pixel_aot_${adapter} COMMAND "${INTAKE_PYTHON}" -B
            "${CMAKE_SOURCE_DIR}/tests/pixel_aot_check.py" draw "${DXC_EXECUTABLE}"
            "$<TARGET_FILE:offscreen_check>" ${adapter})
        set_tests_properties(pixel_aot_${adapter} PROPERTIES TIMEOUT 60)
        add_test(NAME bc7_draw_${adapter} COMMAND "${INTAKE_PYTHON}" -B
            "${CMAKE_SOURCE_DIR}/tests/bc7_draw_check.py" "${DXC_EXECUTABLE}"
            "$<TARGET_FILE:offscreen_check>" ${adapter})
        set_tests_properties(bc7_draw_${adapter} PROPERTIES TIMEOUT 60)
    endforeach()
    set_tests_properties(cpu_native_frame_hardware pixel_aot_hardware bc7_draw_hardware PROPERTIES SKIP_RETURN_CODE 77)
    add_test(NAME compressed_scene_metadata COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/scene_records_check.py" "${CMAKE_CURRENT_BINARY_DIR}/DualUav.cso"
        "${CMAKE_CURRENT_BINARY_DIR}/triangle_ps.cso" "$<TARGET_FILE:root_probe>")
    add_test(NAME root_device_controls_warp COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tools/probe_root_devices.py" --warp-only --bin "$<TARGET_FILE_DIR:root_device_probe>")
    add_test(NAME shader_metadata_probe COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/shader_probe_check.py" "$<TARGET_FILE:root_probe>"
        "${CMAKE_CURRENT_BINARY_DIR}/compute_root.cso" "${CMAKE_CURRENT_BINARY_DIR}/compute_tables.cso"
        "${CMAKE_CURRENT_BINARY_DIR}/triangle_vs.cso" "${DXC_BIN_DIR}/dxil.dll")
    set_tests_properties(shader_metadata_probe PROPERTIES SKIP_RETURN_CODE 77)
    add_test(NAME shader_metadata_controls COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tests/shader_probe_check.py")
    add_test(NAME conditional_import_paths COMMAND "${INTAKE_PYTHON}" -B "${CMAKE_SOURCE_DIR}/tests/intake/paths_check.py")
    add_library(intake_provider SHARED tests/intake/provider.cpp tests/intake/provider.def)
    add_library(intake_delay SHARED tests/intake/provider.cpp tests/intake/delay.def)
    add_executable(intake_candidate tests/intake/candidate.cpp)
    # Build an import library from a name only. No graphics DLL is linked or executed at build time.
    set(graphics_import "${CMAKE_CURRENT_BINARY_DIR}/intake_graphics.lib")
    add_custom_command(OUTPUT "${graphics_import}"
        COMMAND "${CMAKE_AR}" /nologo /machine:x64 "/def:${CMAKE_CURRENT_SOURCE_DIR}/tests/intake/graphics_import.def"
                "/out:${graphics_import}"
        DEPENDS tests/intake/graphics_import.def VERBATIM)
    target_sources(intake_candidate PRIVATE "${graphics_import}")
    target_link_libraries(intake_candidate PRIVATE intake_provider intake_delay delayimp "${graphics_import}")
    target_link_options(intake_candidate PRIVATE /DELAYLOAD:intake_delay.dll)
    add_test(NAME static_exe_intake COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/intake/check.py" "$<TARGET_FILE:intake_candidate>"
        "$<TARGET_FILE:intake_provider>")
    # intake_forward exports six placeholders. The test rewrites a copy so each export is a forwarder;
    # it reads the images and never loads them.
    add_library(intake_forward SHARED tests/intake/provider.cpp tests/intake/forward.def)
    add_executable(intake_forward_caller tests/intake/forward_caller.cpp)
    target_link_libraries(intake_forward_caller PRIVATE intake_forward)
    add_test(NAME bundled_export_forwarders COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/intake/forwarders_check.py" "$<TARGET_FILE:intake_forward_caller>"
        "$<TARGET_FILE:intake_forward>" "$<TARGET_FILE:intake_provider>")
    add_test(NAME layout_chunk_summary COMMAND "${INTAKE_PYTHON}" -B
        "${CMAKE_SOURCE_DIR}/tests/layout_chunks_check.py")
else()
    message(WARNING
        "Python-driven tests, including the CPU fixtures, are disabled because "
        "INTAKE_PYTHON was not found at '${INTAKE_PYTHON}'. "
        "Follow the Python setup under First build in README.md, or set INTAKE_PYTHON "
        "to an interpreter with requirements-dev.txt installed. Then rerun CMake.")
endif()

if(EXISTS "${INTAKE_PYTHON}")
    add_executable(rcas_coordinate_readback tests/rcas_coordinate_readback/readback.cpp)
    target_link_libraries(rcas_coordinate_readback PRIVATE d3d11 d3dcompiler)
    foreach(control IN ITEMS positive negative)
        add_test(NAME rcas_coordinate_${control}_warp COMMAND "${INTAKE_PYTHON}" -B
            "${CMAKE_SOURCE_DIR}/tests/rcas_coordinate_readback/verify.py" "${CMAKE_SOURCE_DIR}"
            --host $<TARGET_FILE:rcas_coordinate_readback> --backend warp --case ${control})
    endforeach()
endif()
